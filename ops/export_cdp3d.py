import bpy
import struct
import datetime
import mathutils
from mathutils import Vector, Matrix

from ..crashday import p3d

if 'bpy' in locals():
    import importlib
    importlib.reload(p3d)


def color_to_int(value):
    return int('%02x%02x%02x' % (int(value[0]*255), int(value[1]*255), int(value[2]*255)), 16)

def error_no_main(self, context):
    self.layout.label(text='Every CD model must have a main mesh!')

def sanitise_mesh_name(name):
    return name.replace(' ', '_')

def get_textures_used(ob):
    textures = []

    m = None

    # Check if there are any materials on the mesh
    for mat in ob.data.materials:
        if mat is not None:
            m = mat
            break

    # if no material was found, add default colwhite.tga material to the object
    if m is None:
        col_white = bpy.data.materials.get('empty material')
        if col_white is None:
            col_white = bpy.data.materials.new('empty material')

            col_white.cdp3d.material_name = 'colwhite'
            col_white.cdp3d.material_type = 'FLAT'

        ob.data.materials.append(col_white)

    for mat in ob.data.materials:
        tn = ''
        if mat.cdp3d.use_texture:
            if mat.node_tree:
                img = mat.node_tree.nodes.get('Image Texture') # get texture used in the material
                if img and img.image: # if exists and has linked texture
                    tn = img.image.name.rsplit( ".", 1 )[ 0 ] # remove extension if present
                    mat.cdp3d.material_name = tn
                else:
                    tn = mat.cdp3d.material_name # otherwise use material preset in cdp3d material properties
            else:
                tn = mat.cdp3d.material_name
        else:
            tn = mat.cdp3d.material_name

        if tn not in textures:
            textures.append(tn)

    return textures


def save_v1(operator, context, filepath='',
            use_selection=True,
            use_mesh_modifiers=True,
            use_empty_for_floor_level=True,
            bbox_mode='MAIN',
            force_main_mesh=False,
            export_log=False):

    # P3D v1 is a single-mesh format. Mesh objects are therefore flattened
    # into one geometry block; object locations are retained relative to main.
    if bpy.ops.object.mode_set.poll():
        bpy.ops.object.mode_set(mode='OBJECT')

    dg = bpy.context.evaluated_depsgraph_get()
    scene_col = bpy.context.scene.collection
    # Blender's export operator normally passes use_selection=True, but P3D v1
    # is a single geometry container and imported/linked models are not always
    # selected after import.  First collect the requested selection; if it does
    # not contain any meshes, fall back to all visible scene meshes.
    objects = []
    all_visible = []
    for ob in bpy.context.scene.objects:
        if not ob.visible_get():
            continue
        all_visible.append(ob)
        if use_selection and not ob.select_get():
            continue
        objects.append(ob)

    mesh_objects = [ob for ob in objects if ob.type == 'MESH']
    if not mesh_objects:
        mesh_objects = [ob for ob in all_visible if ob.type == 'MESH']
        if mesh_objects:
            objects = [ob for ob in all_visible if ob.type in {'MESH', 'LIGHT'}]
    # P3D v1 has no per-mesh name/flags field. Any mesh can therefore be
    # exported as the single geometry block. Prefer 'main' when present,
    # otherwise use the first mesh instead of silently cancelling the export.
    main = next((ob for ob in mesh_objects if ob.name == 'main'), None)
    if main is None:
        main = next((ob for ob in mesh_objects if 'main' in ob.name.lower()), None)
    if main is None and mesh_objects:
        main = mesh_objects[0]
    if main is None:
        operator.report({'ERROR'}, 'No mesh objects to export')
        return {'CANCELLED'}

    main_center = main.location.copy()

    # Blender and P3D v1 use different model bases. The importer converts
    # P3D -> Blender with +90 degrees around X after subtracting the P3D model
    # center. Export performs the exact inverse conversion.
    # Nothing in the Blender scene is modified.
    v1_blender_to_p3d = (
        Matrix.Rotation(-1.5707963267948966, 4, 'X') @
        Matrix.Rotation(-3.141592653589793, 4, 'Z')
    )

    # Keep the original P3D v1 dimensions when exporting an imported model.
    # This is important: re-centering the vertices from their Blender bounds
    # changes the model's world position on a round trip.
    stored_size_x = context.scene.get('p3d_v1_size_x')
    stored_size_y = context.scene.get('p3d_v1_size_y')
    stored_size_z = context.scene.get('p3d_v1_size_z')
    if stored_size_x is not None and stored_size_y is not None and stored_size_z is not None:
        v1_p3d_center = Vector((
            float(stored_size_x) * 0.5,
            float(stored_size_y) * 0.5,
            -float(stored_size_z) * 0.5,
        ))
    else:
        v1_p3d_center = None

    p = p3d.P3D()
    p.version = 1

    # Preserve v1 metadata when exporting an imported v1 model.
    header_hex = context.scene.get('p3d_v1_header_hex')
    if header_hex:
        try:
            p.v1_header = bytes.fromhex(header_hex)[:p3d.P3D_V1_HEADER_SIZE]
        except ValueError:
            pass
    p.v1_post_size_byte = int(context.scene.get('p3d_v1_post_size_byte', 1))

    # Gather geometry first, then pack polygons by texture/material as required
    # by the six per-texture counters in the v1 format.
    raw_polys = []
    vertices = []
    low = [0.0, 0.0, 0.0]
    high = [0.0, 0.0, 0.0]
    have_vertex = False

    for ob in mesh_objects:
        mesh = None
        mesh_owner = ob
        used_temporary_mesh = False
        export_ob = ob
        try:
            if use_mesh_modifiers:
                # IMPORTANT: Object.to_mesh() on the original object does not
                # mean "apply the modifier stack". Evaluate the object through
                # Blender's dependency graph first, then create a mesh from the
                # evaluated object. This makes the V1 checkbox behave the same
                # way as the V2 exporter.
                export_ob = ob.evaluated_get(dg)
                mesh = export_ob.to_mesh(preserve_all_data_layers=True, depsgraph=dg)
                mesh_owner = export_ob
                used_temporary_mesh = mesh is not None

            # If modifier evaluation produced no usable mesh, fall back to the
            # original datablock. This keeps export robust for unusual objects.
            if mesh is None or len(mesh.vertices) == 0 or len(mesh.polygons) == 0:
                if used_temporary_mesh:
                    mesh_owner.to_mesh_clear()
                mesh = ob.data
                mesh_owner = ob
                export_ob = ob
                used_temporary_mesh = False

            # Use the complete object transform, then convert from Blender
            # model space into the P3D v1 basis.  Work on temporary vertex
            # coordinates only; object transforms in the Blender scene are
            # never changed.
            matrix = export_ob.matrix_world.copy()
            matrix.translation -= main_center

            base = len(vertices)
            for v in mesh.vertices:
                blender_co = matrix @ v.co
                co = v1_blender_to_p3d @ Vector(blender_co)
                if v1_p3d_center is not None:
                    co += v1_p3d_center
                pos = (co.x, co.y, co.z)
                vertices.append(list(pos))
                if not have_vertex:
                    low[:] = pos
                    high[:] = pos
                    have_vertex = True
                else:
                    for axis in range(3):
                        low[axis] = min(low[axis], pos[axis])
                        high[axis] = max(high[axis], pos[axis])

            mesh.calc_loop_triangles()
            uv_layer = mesh.uv_layers.active
            if uv_layer is None:
                uv_layer = mesh.uv_layers.new()

            for tri in mesh.loop_triangles:
                if len(tri.loops) != 3:
                    continue
                # A Blender mesh may legitimately have no material slots.
                # P3D v1 still requires those polygons to be exported, so use
                # the same safe defaults as the original addon: Gouraud +
                # colwhite. Do not discard a triangle just because its
                # material index has no corresponding slot.
                mat = None
                if 0 <= tri.material_index < len(mesh.materials):
                    mat = mesh.materials[tri.material_index]

                material_type = 'GOURAUD'
                texture = 'colwhite'
                if mat is not None:
                    cdp3d = getattr(mat, 'cdp3d', None)
                    material_type = getattr(cdp3d, 'material_type', 'GOURAUD')
                    texture = getattr(cdp3d, 'material_name', 'colwhite') or 'colwhite'
                    use_texture = bool(getattr(cdp3d, 'use_texture', False))
                    if use_texture and mat.node_tree:
                        image_node = mat.node_tree.nodes.get('Image Texture')
                        if image_node and image_node.image:
                            texture = image_node.image.name.rsplit('.', 1)[0]

                if material_type not in {
                    'FLAT', 'FLAT_METAL', 'GOURAUD',
                    'GOURAUD_METAL', 'GOURAUD_METAL_ENV', 'SHINING'
                }:
                    material_type = 'GOURAUD'

                loops = tri.loops
                uv = uv_layer.data
                pol = p3d.Polygon()
                pol.texture = texture
                pol.material = material_type
                pol.p1 = base + tri.vertices[0]
                pol.p2 = base + tri.vertices[1]
                pol.p3 = base + tri.vertices[2]
                pol.u1, pol.v1 = uv[loops[0]].uv
                pol.u2, pol.v2 = uv[loops[1]].uv
                pol.u3, pol.v3 = uv[loops[2]].uv
                raw_polys.append(pol)
        finally:
            if used_temporary_mesh:
                mesh_owner.to_mesh_clear()

    if not vertices or not raw_polys:
        # operator.report(
            # {'ERROR'},
            # 'P3D v1 export found no geometry: {} vertices, {} polygons from {} mesh object(s)'.format(
                # len(vertices), len(raw_polys), len(mesh_objects)))
        # print('P3D v1: mesh objects:', [ob.name for ob in mesh_objects])
        # print('P3D v1: vertices:', len(vertices), 'polygons:', len(raw_polys))
        return {'CANCELLED'}

    # Do NOT normalize/recenter an imported model by its current bounding box.
    # That would change its position on a round trip. For a newly created
    # Blender model (without stored P3D dimensions), build the canonical P3D
    # origin once from its transformed bounds.
    if v1_p3d_center is None:
        v1_p3d_center = Vector((
            (low[0] + high[0]) * 0.5,
            (low[1] + high[1]) * 0.5,
            -(high[2] - low[2]) * 0.5,
        ))
        for pos in vertices:
            pos[0] += v1_p3d_center.x
            pos[1] += v1_p3d_center.y
            pos[2] += v1_p3d_center.z
        low[0] += v1_p3d_center.x
        low[1] += v1_p3d_center.y
        low[2] += v1_p3d_center.z
        high[0] += v1_p3d_center.x
        high[1] += v1_p3d_center.y
        high[2] += v1_p3d_center.z

    p3d_offset = Vector((0.0, 0.0, 0.0))

    # Build texture list in stable order.
    textures = []
    for poly in raw_polys:
        if poly.texture not in textures:
            textures.append(poly.texture)
    if len(textures) > 255:
        raise RuntimeError('P3D v1 supports at most 255 textures')

    p.textures = textures
    p.num_textures = len(textures)

    ordered = []
    infos = []
    mode_names = (
        'FLAT', 'FLAT_METAL', 'GOURAUD',
        'GOURAUD_METAL', 'GOURAUD_METAL_ENV', 'SHINING'
    )

    for texture in textures:
        info = p3d.TextureInfo()
        info.texture_start = len(ordered)
        for mode_index, mode in enumerate(mode_names):
            for poly in raw_polys:
                if poly.texture == texture and poly.material == mode:
                    ordered.append(poly)
                    setattr(info, (
                        'num_flat', 'num_flat_metal', 'num_gouraud',
                        'num_gouraud_metal', 'num_gouraud_metal_env',
                        'num_shining')[mode_index],
                            getattr(info, (
                                'num_flat', 'num_flat_metal', 'num_gouraud',
                                'num_gouraud_metal', 'num_gouraud_metal_env',
                                'num_shining')[mode_index]) + 1)
        infos.append(info)

    # Unknown material types are exported as Gouraud, matching the safe default
    # used by the Blender material property.
    accounted = len(ordered)
    if accounted != len(raw_polys):
        for poly in raw_polys:
            if poly not in ordered:
                poly.material = 'GOURAUD'
                for texture in textures:
                    if poly.texture == texture:
                        # Rebuild this texture's Gouraud count below.
                        break
        ordered = []
        infos = []
        for texture in textures:
            info = p3d.TextureInfo()
            info.texture_start = len(ordered)
            for mode_index, mode in enumerate(mode_names):
                for poly in raw_polys:
                    if poly.texture == texture and poly.material == mode:
                        ordered.append(poly)
                        attr = (
                            'num_flat', 'num_flat_metal', 'num_gouraud',
                            'num_gouraud_metal', 'num_gouraud_metal_env',
                            'num_shining')[mode_index]
                        setattr(info, attr, getattr(info, attr) + 1)
            infos.append(info)

    m = p3d.Mesh()
    m.name = 'main'
    m.flags = 1
    m.pos = [0.0, 0.0, 0.0]
    m.vertices = vertices
    m.num_vertices = len(vertices)
    m.polys = ordered
    m.num_polys = len(ordered)
    m.texture_infos = infos
    m.length = high[0] - low[0]
    m.depth = high[1] - low[1]
    m.height = high[2] - low[2]

    p.meshes = [m]
    p.num_meshes = 1
    if stored_size_x is not None and stored_size_y is not None and stored_size_z is not None:
        p.length = float(stored_size_x)
        p.height = float(stored_size_y)
        p.depth = float(stored_size_z)
    else:
        p.length = m.length
        p.height = m.height
        p.depth = m.depth

    if use_empty_for_floor_level:
        floor = bpy.data.objects.get('floor_level')
        if floor is not None:
            p.height = -(floor.location.z - main_center.z) * 2.0

    p.lights = []
    p.v1_materials = []
    for ob in objects:
        if ob.type != 'LIGHT':
            continue
        light = p3d.Light()
        light_blender = Vector((
            ob.location.x - main_center.x,
            ob.location.y - main_center.y,
            ob.location.z - main_center.z
        ))
        # Lights use the same P3D model-space origin as the mesh.
        # The mesh export converts Blender-local coordinates back into P3D
        # and then adds the original P3D model center. Do exactly the same
        # for lights; otherwise their positions are shifted on re-export.
        light_p3d = v1_blender_to_p3d @ light_blender
        if v1_p3d_center is not None:
            light_p3d += v1_p3d_center
        light.pos = [light_p3d.x, light_p3d.y, light_p3d.z]
        light.range = ob.data.energy
        light.color = color_to_int(ob.data.color)
        light.show_corona = ob.data.cdp3d.corona
        light.show_lens_flares = ob.data.cdp3d.lens_flares
        light.lightup_environment = ob.data.cdp3d.lightup_environment
        p.lights.append(light)
        p.v1_materials.append({
            'position': light.pos,
            'range': light.range,
            'packed_color': light.color,
            'corona': int(light.show_corona),
            'lens_flares': int(light.show_lens_flares),
            'lightup': int(light.lightup_environment),
        })
    p.num_lights = len(p.lights)

    try:
        with open(filepath, 'wb') as file:
            p.write(file)
    except (OSError, ValueError, struct.error) as exc:
        # operator.report({'ERROR'}, 'P3D v1 export failed: {}'.format(exc))
        # print('P3D v1 export failed:', exc)
        return {'CANCELLED'}

    print('P3D v1 exported:', filepath)
    # operator.report({'INFO'}, 'P3D v1 exported successfully')

    # Store metadata on the scene for subsequent v1 re-export.
    context.scene['p3d_v1_header_hex'] = p.v1_header.hex()
    context.scene['p3d_v1_post_size_byte'] = p.v1_post_size_byte
    return {'FINISHED'}


def save(operator,
         context, filepath='',
         use_selection=True,
         use_mesh_modifiers=True,
         use_empty_for_floor_level=True,
         bbox_mode='MAIN',
         force_main_mesh=False,
         export_log=True,
         format_version='V2'):

    if format_version == 'V1':
        return save_v1(operator, context, filepath, use_selection,
                       use_mesh_modifiers, use_empty_for_floor_level,
                       bbox_mode, force_main_mesh, export_log)

    # get the folder where file will be saved and add a log in that folder
    work_path = '\\'.join(filepath.split('\\')[0:-1])

    log_file = None
    if export_log:
        log_file = open(work_path + '//export-log.txt', 'a')
    date = datetime.datetime.now()
    if log_file:
        log_file.write('Started exporting on {}\nFile path: {}\n'.format(date.strftime('%d-%m-%Y %H:%M:%S'), filepath))
    print('\nExporting file to {}'.format(filepath))

    # create empty p3d model
    p = p3d.P3D()

    # exit edit mode
    if bpy.ops.object.mode_set.poll():
        bpy.ops.object.mode_set(mode='OBJECT')

    # get dependencies graph for applying modifiers
    dg = bpy.context.evaluated_depsgraph_get()

    col = bpy.context.scene.collection

    # stores the list of exported meshes - useful for modders to define in .cca
    exported_meshes_string = ''

    # store list oj objects to export
    objects = []
    for ob in col.all_objects:
         if ob.visible_get():
            if not use_selection:
                # apply modifiers if needed and store object
                objects.append(ob.evaluated_get(dg) if use_mesh_modifiers else ob)
            elif ob.select_get():
                objects.append(ob.evaluated_get(dg) if use_mesh_modifiers else ob)

    # store the list of all the used textures
    p.textures = []

    # store main, shadow and collision meshes
    main = None
    shad = None
    coll = None

    main_like = None
    any = None

    # find the main, sadow and coll mesh of the model
    for ob in objects:
        if ob.type == 'MESH':
            any = ob
            p.textures = list(set(p.textures + get_textures_used(ob)))
            if 'main' in ob.name:
                main_like = ob
            if ob.name == 'main':
                main = ob
                main_like = ob
                any = ob
            if ob.name == 'mainshad':
                shad = ob
            if ob.name == 'maincoll':
                coll = ob

    # save the amount of textures used in p3d model
    p.num_textures = len(p.textures)

    # p3d models must have a main mesh
    if main is None:
        if not force_main_mesh:
            bpy.context.window_manager.popup_menu(error_no_main, title='No main mesh', icon='ERROR')
            print('!!! Failed to export p3d. No main mesh found.')
            if log_file:
                log_file.write('!!! Failed to export p3d. No main mesh found.\n')
                log_file.close()
            return {'CANCELLED'}
        else:
            if main_like is not None:
                print('Found main like mesh: {}. Using as main'.format(main_like.name))
                main = main_like
            elif any is not None:
                print('Found some mesh: {}. Using as main'.format(any.name))
                main = any
            else:
                print('!!! Failed to export p3d. No meshes found.')
                if log_file:
                    log_file.write('!!! Failed to export p3d. No meshes found.\n')
                    log_file.close()
                return {'CANCELLED'}

    if shad is None:
        print('! Shadow mesh was not found, using main mesh for shadow.')
        if log_file:
            log_file.write('! Shadow mesh was not found, using main mesh for shadow.\n')
    if coll is None:
        print('! Collision mesh was not found, using main mesh for collisions.')
        if log_file:
            log_file.write('! Collision mesh was not found, using main mesh for collisions.\n')

    # the main mesh in p3d is always at 0.0.
    # this means we need to move all other models alongside main mesh
    main_center = main.location
    
    # iterate through all objects in the scene and save into p3d model
    p.meshes = []
    p.lights = []
    for ob in objects:
        if ob.type == 'LIGHT':
            p.num_lights += 1

            light = p3d.Light()
            light.name = sanitise_mesh_name(ob.name)
            light.pos = ob.location - main_center
            light.range = ob.data.energy
            light.color = color_to_int(ob.data.color)

            light.show_corona = ob.data.cdp3d.corona
            light.show_lens_flares = ob.data.cdp3d.lens_flares
            light.lightup_environment = ob.data.cdp3d.lightup_environment

            p.lights.append(light)

        if ob.type == 'MESH':
            m = p3d.Mesh()

            m.name = sanitise_mesh_name(ob.name)
            m.pos = ob.location - main_center

            mesh = ob.to_mesh()

            # save vertex positions
            scale = ob.scale

            lowx = 0.0
            highx = 0.0
            lowy = 0.0
            highy = 0.0
            lowz = 0.0
            highz = 0.0

            if len(m.vertices) > 0:
                lowx = highx = m.vertices[0].co[0]*scale[0]
                lowy = highy = m.vertices[0].co[1]*scale[1]
                lowz = highz = m.vertices[0].co[2]*scale[2]

            # save vertices and calculate mesh bounds
            m.vertices = []
            for v in mesh.vertices:
                pos = [a*b for a,b in zip(v.co,scale)]
                if lowx > pos[0]: lowx = pos[0]
                if highx < pos[0]: highx = pos[0]
                if lowy > pos[1]: lowy = pos[1]
                if highy < pos[1]: highy = pos[1]
                if lowz > pos[2]: lowz = pos[2]
                if highz < pos[2]: highz = pos[2]
                m.vertices.append(pos)

            m.num_vertices = len(m.vertices)

            m.length = highx - lowx
            m.height = highz - lowz
            m.depth = highy - lowy

            if bbox_mode == 'ALL':
                p.height = max(p.height, m.height)
                p.length = max(p.length, max(highx, -lowx) * 2)
                p.depth = max(p.depth, max(highy, -lowy) * 2)

            # save model bounds
            if ob == main:
                floor_level = bpy.data.objects.get('floor_level')

                m.name = sanitise_mesh_name('main')

                if floor_level is not None and use_empty_for_floor_level:
                    fl_pos = floor_level.location
                    m.height = -(fl_pos - main.location)[2]*2

                # this is size calculation which is done in original p3d
                # but this breaks collision for non-symmetrical tiles 
                if bbox_mode == 'MAIN':
                    p.length = m.length
                    p.height = m.height
                    p.depth = m.depth

                    # while this looks dumb, this is how original makep3d works
                    if p.length >= 19.95 and p.length <= 20.05: p.length = 20
                    if p.length >= 39.95 and p.length <= 40.05: p.length = 40
                    if p.depth >= 19.95 and p.depth <= 20.05: p.depth = 20
                    if p.depth >= 39.95 and p.depth <= 40.05: p.depth = 40

                # this would fix non-symmetrical tile bounding-box
                #p.height = m.height
                #p.length = max(highx, -lowx) * 2
                #p.depth = max(highy, -lowy) * 2

            items = mesh.cdp3d.bl_rna.properties['flags'].enum_items

            for flag in mesh.cdp3d.flags:
                m.flags |= items[flag].value

            # save the flags
            if ob == main:
                m.flags |= 1
                if shad is None:
                    m.flags |= 4
                if coll is None:
                    m.flags |= 8
            elif ob == shad:
                m.flags ^= 2
                m.flags |= 4
            elif ob == coll:
                m.flags ^= 2
                m.flags |= 8

            mesh.calc_loop_triangles()

            # save polys in blender order and texture infos
            m.texture_infos = [p3d.TextureInfo() for i in range(p.num_textures)]
            polys = []
            if len(mesh.uv_layers) == 0:
                mesh.uv_layers.new()
            for uv_layer in mesh.uv_layers:
                for tri in mesh.loop_triangles:
                    if len(tri.loops) != 3:
                        break
                    pol = p3d.Polygon()

                    # texture info
                    # TODO: if a polygon is assigned to a material which was deleted this will error
                    pol.texture = ob.data.materials[tri.material_index].cdp3d.material_name
                    pol.material = ob.data.materials[tri.material_index].cdp3d.material_type
                    
                    i = p.textures.index(pol.texture)
                    if pol.material == 'FLAT':
                        m.texture_infos[i].num_flat += 1
                    if pol.material == 'FLAT_METAL':
                        m.texture_infos[i].num_flat_metal += 1
                    if pol.material == 'GOURAUD':
                        m.texture_infos[i].num_gouraud += 1
                    if pol.material == 'GOURAUD_METAL':
                        m.texture_infos[i].num_gouraud_metal += 1
                    if pol.material == 'GOURAUD_METAL_ENV':
                        m.texture_infos[i].num_gouraud_metal_env += 1
                    if pol.material == 'SHINING':
                        m.texture_infos[i].num_shining += 1

                    # polygon info
                    pol.p1 = tri.vertices[0]
                    pol.u1, pol.v1 = uv_layer.data[tri.loops[0]].uv

                    pol.p2 = tri.vertices[1]
                    pol.u2, pol.v2 = uv_layer.data[tri.loops[1]].uv

                    pol.p3 = tri.vertices[2]
                    pol.u3, pol.v3 = uv_layer.data[tri.loops[2]].uv

                    polys.append(pol)

            # reorder polys into CD format, to align texture infos
            m.polys = []
            for t in range(len(p.textures)):
                if t > 0:
                    m.texture_infos[t].texture_start = m.texture_infos[t-1].texture_start 
                    m.texture_infos[t].texture_start += m.texture_infos[t-1].num_flat
                    m.texture_infos[t].texture_start += m.texture_infos[t-1].num_flat_metal
                    m.texture_infos[t].texture_start += m.texture_infos[t-1].num_gouraud
                    m.texture_infos[t].texture_start += m.texture_infos[t-1].num_gouraud_metal
                    m.texture_infos[t].texture_start += m.texture_infos[t-1].num_gouraud_metal_env
                    m.texture_infos[t].texture_start += m.texture_infos[t-1].num_shining

                for i, pol in enumerate(polys):
                    if pol.texture == p.textures[t] and pol.material == 'FLAT':
                        m.polys.append(pol)
                
                for i, pol in enumerate(polys):
                    if pol.texture == p.textures[t] and pol.material == 'FLAT_METAL':
                        m.polys.append(pol)

                for i, pol in enumerate(polys):
                    if pol.texture == p.textures[t] and pol.material == 'GOURAUD':
                        m.polys.append(pol)

                for i, pol in enumerate(polys):
                    if pol.texture == p.textures[t] and pol.material == 'GOURAUD_METAL':
                        m.polys.append(pol)

                for i, pol in enumerate(polys):
                    if pol.texture == p.textures[t] and pol.material == 'GOURAUD_METAL_ENV':
                        m.polys.append(pol)

                for i, pol in enumerate(polys):
                    if pol.texture == p.textures[t] and pol.material == 'SHINING':
                        m.polys.append(pol)


            m.num_polys = len(m.polys)

            if len(m.vertices) == 0 or len(m.polys) == 0:
                message = 'Can\'t export empty mesh: {}. {} vertices, {} polys. Ignoring'.format(m.name, len(m.vertices), len(m.polys))
                print(message)
                if log_file:
                    log_file.write(message)
            else:
                p.num_meshes += 1
                p.meshes.append(m)
                exported_meshes_string += ob.name + ' '

    # save p3d into file
    file = open(filepath, 'wb')
    print(p)
    p.write(file)
    file.close()

    print('p3d exported')
    if log_file:
        log_file.write('Meshes: {}\n'.format(exported_meshes_string))
        log_file.write('Finished p3d export.\n\n')
        log_file.close()

    return {'FINISHED'}

def save_pos(f, col, offset, name):
    obj = col.objects.get(name)
    pos = (0.0,0.0,0.0)
    if obj is not None:
        p = obj.location
        pos = (p[0] - offset[0], p[2] - offset[2], p[1] - offset[1])

    f.write('{:.4g} {:.4g} {:.4g} \t\t\t # {}{}\n'.format(pos[0], pos[1], pos[2], '!!!NOT FOUND ON EXPORT  ' if obj is None else '', name))

def save_pos2(f, col, offset, name):
    obj = col.objects.get(name)
    pos = (0.0,0.0,0.0)
    if obj is not None:
        p = obj.location
        pos = (p[0] - offset[0], p[2] - offset[2], p[1] - offset[1])

    f.write('{:.4g} \t\t\t # {}{}\n'.format(pos[2], '!!!NOT FOUND ON EXPORT  ' if obj is None else '', name))

def save_cca(operator,
         context, filepath=''):

    f = open(filepath, 'w')
    col = bpy.context.scene

    exported_meshes_string = ''

    # store main, shadow and collision meshes
    main = None
    mpos = (0.0,0.0,0.0)

    # find the main mesh of the model
    for ob in col.collection.all_objects:
        if ob.type == 'MESH':
            exported_meshes_string += ob.name + ' '
            if ob.name == 'main':
                main = ob
                mpos = main.location

    # p3d models must have a main mesh
    if main is None:
        print('!!! No main mesh found, .cca values might be wrong if main is not centered.')
        f.write('!!! No main mesh found, .cca values might be wrong if main is not centered.')   

    f.write('Meshes: {}\n\n'.format(exported_meshes_string))

    save_pos(f, col, mpos, 'center_of_gravity_pos')
    f.write('\n')
    save_pos(f, col, mpos, 'left_upper_wheel_pos')
    save_pos(f, col, mpos, 'right_lower_wheel_pos')
    save_pos(f, col, mpos, 'minigun_pos')
    f.write('0.0\t\t\t # Angle of minigun (negative values for downpointing)\n')
    save_pos(f, col, mpos, 'mines_pos')
    save_pos(f, col, mpos, 'missiles_pos')
    save_pos(f, col, mpos, 'driver_pos')
    save_pos(f, col, mpos, 'exhaust_pos')
    save_pos(f, col, mpos, 'exhaust2_pos')
    save_pos(f, col, mpos, 'flag_pos')
    save_pos(f, col, mpos, 'bomb_pos')
    save_pos(f, col, mpos, 'cockpit_cam_pos')
    save_pos(f, col, mpos, 'roof_cam_pos')
    save_pos(f, col, mpos, 'hood_cam_pos')
    save_pos(f, col, mpos, 'bumper_cam_pos')
    save_pos(f, col, mpos, 'rear_view_cam_pos')
    save_pos(f, col, mpos, 'left_side_cam_pos')
    save_pos(f, col, mpos, 'right_side_cam_pos')
    save_pos(f, col, mpos, 'driver1_cam_pos')
    save_pos(f, col, mpos, 'driver2_cam_pos')
    save_pos(f, col, mpos, 'driver3_cam_pos')
    save_pos(f, col, mpos, 'steering_wheel_pos')
    save_pos(f, col, mpos, 'car_cover_pos')
    save_pos2(f, col, mpos, 'engine_pos')

    f.close()
    return {'FINISHED'}