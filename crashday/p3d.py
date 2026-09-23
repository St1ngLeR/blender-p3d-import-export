import struct
import io

# TODO:
# - add error checking for struct reading\writing

def rf(file, format):
    answer = struct.unpack(format, file.read(struct.calcsize(format)))
    return answer[0] if len(answer) == 1 else answer

def rf_str(file):
    string = b''
    while True:
        char = struct.unpack('<c', file.read(1))[0]
        if char == b'\x00':
            break
        string += char
    return str(string, 'utf-8', 'replace')

def wf(file, format, *args):
    file.write(struct.pack(format, *args))

def wf_str(file, st):
    wf(file, '<%ds' % (len(st)+1), st.encode('ASCII', 'replace'))

def write_block(file, signature, data_func):
    """Write a block with the given signature, followed by its size (4 bytes)
       and then the data produced by data_func."""
    buf = io.BytesIO()
    data_func(buf)
    data = buf.getvalue()
    file.write(signature)
    wf(file, '<I', len(data))
    file.write(data)

class TextureInfo:
    def __init__(self):
        self.texture_start = 0
        self.num_flat = 0
        self.num_flat_metal = 0
        self.num_gouraud = 0
        self.num_gouraud_metal = 0
        self.num_gouraud_metal_env = 0
        self.num_shining = 0

    def __str__(self):
        return 'tex info: {} {} {} {} {} {} {}'.format(
            self.texture_start, self.num_flat, self.num_flat_metal,
            self.num_gouraud, self.num_gouraud_metal, self.num_gouraud_metal_env,
            self.num_shining)

    def read(self, file):
        (self.texture_start,
        self.num_flat,
        self.num_flat_metal,
        self.num_gouraud,
        self.num_gouraud_metal,
        self.num_gouraud_metal_env,
        self.num_shining) = rf(file, '<7H')

    def write(self, file):
        wf(file, '<7H',
            self.texture_start,
            self.num_flat,
            self.num_flat_metal,
            self.num_gouraud,
            self.num_gouraud_metal,
            self.num_gouraud_metal_env,
            self.num_shining
            )

class Light:
    def __init__(self):
        self.name = 'light'
        self.pos = [0.0, 0.0, 0.0]
        self.range = 1.0
        self.color = 255

        self.show_corona = True
        self.show_lens_flares = True
        self.lightup_environment = True

    def __str__(self):
        formated_pos = ['{0:0.2f}'.format(i) for i in self.pos]
        return '''{}\nrange: {:.2f}, color: #{:x}\ncorona {}, flares {}, environment {}\npos: {}\n'''.format(
            self.name[0] if type(self.name) is tuple else self.name, 
            self.range, self.color, 
            self.show_corona, self.show_lens_flares, self.lightup_environment,
            formated_pos
        )

    def read(self, file):
        self.name = rf_str(file)

        (self.pos[0], self.pos[2], self.pos[1],
        self.range, self.color,
        self.show_corona, self.show_lens_flares, 
        self.lightup_environment) = rf(file, '<4fi3B')

    def write(self, file):
        wf_str(file, self.name.lower())
        wf(file, '<4fi3B', self.pos[0], self.pos[2], self.pos[1],
        self.range, self.color, self.show_corona, 
        self.show_lens_flares, self.lightup_environment)

class Polygon:
    def __init__(self):
        self.texture = ''
        self.material = 0

        self.p1 = 0
        self.u1 = 0.0
        self.v1 = 0.0

        self.p2 = 0
        self.u2 = 0.0
        self.v2 = 0.0

        self.p3 = 0
        self.u3 = 0.0
        self.v3 = 0.0

    def __str__(self):
        return str(self.__class__) + ': ' + str(self.__dict__)

    def read(self, file):
        (self.p1, self.u1, self.v1,
        self.p3, self.u3, self.v3,
        self.p2, self.u2, self.v2
        ) = rf(file, '<H2fH2fH2f')

        self.v1 = 1.0 - self.v1
        self.v2 = 1.0 - self.v2
        self.v3 = 1.0 - self.v3

    def write(self, file):
        wf(file, '<H2fH2fH2f', 
        self.p1, self.u1, 1.0 - self.v1,
        self.p3, self.u3, 1.0 - self.v3,
        self.p2, self.u2, 1.0 - self.v2
        )

class Mesh:
    def __init__(self):
        self.name = 'mesh'
        self.flags = 0
        self.pos = [0.0, 0.0, 0.0]

        self.length = 0.0
        self.height = 0.0
        self.depth = 0.0

        #this is not in the default p3d format
        self.materials_used = []

        self.texture_infos = []

        self.num_vertices = 0
        self.vertices = []

        self.num_polys = 0
        self.polys = []

    def __str__(self):
        formated_pos = ['{0:0.2f}'.format(i) for i in self.pos]
        return '''Mesh: {}\nflags: {}, vertices: {}, polys: {}
pos: {}\nsize: {:.2f} {:.2f} {:.2f} \n'''.format(
            self.name[0] if type(self.name) is tuple else self.name, 
            self.flags, self.num_vertices, self.num_polys,
            formated_pos, 
            self.length, self.height, self.depth
        )

    def read(self, file, textures, num_textures):
        def r(format):
            return rf(file, format)

        self.name = rf_str(file)

        (self.flags, 
        self.pos[0], self.pos[2], self.pos[1],
        self.length, self.height, 
        self.depth) = rf(file, '<i6f')

        self.texture_infos = []
        for i in range(num_textures):
            tex_info = TextureInfo()
            tex_info.read(file)
            self.texture_infos.append(tex_info)

        self.num_vertices = r('<H')
        self.vertices = []
        for v in range(self.num_vertices):
            vp = r('<3f')
            self.vertices.append((vp[0], vp[2], vp[1]))

        self.num_polys = r('<H')
        self.polys = []
        for p in range(self.num_polys):
            poly = Polygon()
            poly.read(file)
            self.polys.append(poly)

        #at this point we have read everything but we need to fill
        #texture and materials of every polygon from the packed data
        #of the p3d model
        self.materials_used = []
        for ji, j in enumerate(self.texture_infos):
            polys_in_tex = j.texture_start

            def add_material_type(name, amount, polys_in_tex):
                for n in range(amount):
                    if((name, textures[ji]) not in self.materials_used):
                        self.materials_used.append((name, textures[ji]))
                    self.polys[polys_in_tex + n].material = name
                    self.polys[polys_in_tex + n].texture = textures[ji]

                return polys_in_tex + amount

            polys_in_tex = add_material_type('FLAT', j.num_flat, polys_in_tex)
            polys_in_tex = add_material_type('FLAT_METAL', j.num_flat_metal, polys_in_tex)
            polys_in_tex = add_material_type('GOURAUD', j.num_gouraud, polys_in_tex)
            polys_in_tex = add_material_type('GOURAUD_METAL', j.num_gouraud_metal, polys_in_tex)
            polys_in_tex = add_material_type('GOURAUD_METAL_ENV', j.num_gouraud_metal_env, polys_in_tex)
            polys_in_tex = add_material_type('SHINING', j.num_shining, polys_in_tex) 

    def write(self, file):
        # This method writes only the mesh data (without the SUBMESH header).
        # The caller is responsible for wrapping it in a SUBMESH block.
        def w(format, *args):
            wf(file, format, *args)

        def w_str(st):
            wf_str(file, st)

        w_str(self.name.lower())

        w('<I6f', self.flags,
        self.pos[0], self.pos[2], self.pos[1],
        self.length, self.height, self.depth
        )

        for ti in self.texture_infos:
            ti.write(file)

        if self.num_vertices != len(self.vertices):
            print('Counted num_vertices differs from actual amount of vertices! Report this error!')
            self.num_vertices = len(self.vertices)
        w('<H', self.num_vertices)
        for v in self.vertices:
            w('<3f', v[0], v[2], v[1])

        if self.num_polys != len(self.polys):
            print('Counted num_polys differs from actual amount of polys! Report this error!')
            self.num_polys = len(self.polys)
        w('<H', self.num_polys)
        for p in self.polys:
            p.write(file)


P3D_V1_HEADER_SIZE = 18
P3D_V1_TEXTURE_NAME_SIZE = 18
P3D_V1_DEFAULT_HEADER = bytes(P3D_V1_HEADER_SIZE)


def _read_fixed_ascii(file, size):
    raw = file.read(size)
    if len(raw) != size:
        raise EOFError("Unexpected end of P3D v1 file")
    return raw.split(b'\0', 1)[0].decode('ascii', 'replace')


def _read_v1(file, model):
    signature = file.read(4)
    if signature != b'P3D\x01':
        raise ValueError('Not a P3D version 1 file')

    model.version = 1
    model.v1_header = file.read(P3D_V1_HEADER_SIZE)
    if len(model.v1_header) != P3D_V1_HEADER_SIZE:
        raise EOFError('Truncated P3D v1 header')

    model.length, model.height, model.depth = rf(file, '<3f')
    model.v1_post_size_byte = rf(file, '<B')
    model.num_textures = rf(file, '<B')
    model.textures = []
    model.v1_texture_groups = []

    for _ in range(model.num_textures):
        first_face = rf(file, '<H')
        name = _read_fixed_ascii(file, P3D_V1_TEXTURE_NAME_SIZE)
        counts = rf(file, '<6h')
        texture = name + '.cbm'
        model.textures.append(texture)
        ti = TextureInfo()
        ti.texture_start = first_face
        (ti.num_flat, ti.num_flat_metal, ti.num_gouraud,
         ti.num_gouraud_metal, ti.num_gouraud_metal_env,
         ti.num_shining) = counts
        model.v1_texture_groups.append(ti)

    mesh = Mesh()
    mesh.name = 'main'
    mesh.flags = 1
    mesh.pos = [0.0, 0.0, 0.0]
    mesh.length = model.length
    mesh.height = model.height
    mesh.depth = model.depth
    mesh.texture_infos = model.v1_texture_groups

    mesh.num_vertices = rf(file, '<h')
    if mesh.num_vertices <= 0:
        raise ValueError('P3D v1 NumVertices must be > 0')
    mesh.vertices = [list(rf(file, '<3f')) for _ in range(mesh.num_vertices)]

    mesh.num_polys = rf(file, '<h')
    if mesh.num_polys <= 0:
        raise ValueError('P3D v1 NumPolys must be > 0')
    mesh.polys = []
    for _ in range(mesh.num_polys):
        poly = Polygon()
        # Version 1 stores P1, P2, P3 in this order and does not use
        # the version-2 Y/Z or winding conversion.
        (poly.p1, poly.u1, poly.v1,
         poly.p2, poly.u2, poly.v2,
         poly.p3, poly.u3, poly.v3) = rf(file, '<h2fh2fh2f')
        mesh.polys.append(poly)

    # Resolve packed texture/material groups exactly as the C++ runtime does.
    mesh.materials_used = []
    for ti_index, ti in enumerate(mesh.texture_infos):
        cursor = ti.texture_start

        def add_material_type(name, amount):
            nonlocal cursor
            for _ in range(max(0, amount)):
                if cursor >= len(mesh.polys):
                    raise ValueError('P3D v1 texture group exceeds polygon count')
                pair = (name, model.textures[ti_index])
                if pair not in mesh.materials_used:
                    mesh.materials_used.append(pair)
                mesh.polys[cursor].material = name
                mesh.polys[cursor].texture = model.textures[ti_index]
                cursor += 1

        add_material_type('FLAT', ti.num_flat)
        add_material_type('FLAT_METAL', ti.num_flat_metal)
        add_material_type('GOURAUD', ti.num_gouraud)
        add_material_type('GOURAUD_METAL', ti.num_gouraud_metal)
        add_material_type('GOURAUD_METAL_ENV', ti.num_gouraud_metal_env)
        add_material_type('SHINING', ti.num_shining)

    # Version 1 calls these embedded lights/materials. They have no names.
    model.num_lights = rf(file, '<h')
    if model.num_lights < 0:
        raise ValueError('Negative P3D v1 material/light count')
    model.lights = []
    model.v1_materials = []
    for index in range(model.num_lights):
        x, y, z, light_range, packed_color, corona, flares, lightup = \
            rf(file, '<4fIBBB')
        light = Light()
        light.name = 'light_{:02d}'.format(index)
        light.pos = [x, y, z]
        light.range = light_range
        light.color = packed_color
        light.show_corona = bool(corona)
        light.show_lens_flares = bool(flares)
        light.lightup_environment = bool(lightup)
        model.lights.append(light)
        model.v1_materials.append({
            'position': [x, y, z],
            'range': light_range,
            'packed_color': packed_color,
            'corona': corona,
            'lens_flares': flares,
            'lightup': lightup,
        })

    model.num_meshes = 1
    model.meshes = [mesh]


def _v1_texture_base(name):
    name = str(name)
    for ext in ('.cbm', '.tga', '.dds'):
        if name.lower().endswith(ext):
            name = name[:-len(ext)]
            break
    return name


def _write_v1(file, model):
    file.write(b'P3D')
    file.write(b'\x01')
    header = bytes(getattr(model, 'v1_header', P3D_V1_DEFAULT_HEADER))
    if len(header) != P3D_V1_HEADER_SIZE:
        header = (header + bytes(P3D_V1_HEADER_SIZE))[:P3D_V1_HEADER_SIZE]
    file.write(header)

    wf(file, '<3fB', float(model.length), float(model.height),
       float(model.depth), int(getattr(model, 'v1_post_size_byte', 1)) & 0xFF)

    textures = list(model.textures)
    if len(textures) > 255:
        raise ValueError('P3D v1 supports at most 255 textures')
    wf(file, '<B', len(textures))

    mesh = model.meshes[0] if model.meshes else None
    if mesh is None:
        raise ValueError('P3D v1 requires one mesh')

    infos = list(mesh.texture_infos)
    if len(infos) != len(textures):
        raise ValueError('P3D v1 texture group count does not match texture count')

    for ti, texture in zip(infos, textures):
        name = _v1_texture_base(texture).encode('ascii', 'replace')[:P3D_V1_TEXTURE_NAME_SIZE]
        name = name + bytes(P3D_V1_TEXTURE_NAME_SIZE - len(name))
        wf(file, '<H', int(ti.texture_start) & 0xFFFF)
        file.write(name)
        wf(file, '<6h', int(ti.num_flat), int(ti.num_flat_metal),
           int(ti.num_gouraud), int(ti.num_gouraud_metal),
           int(ti.num_gouraud_metal_env), int(ti.num_shining))

    if len(mesh.vertices) > 32767 or len(mesh.polys) > 32767:
        raise ValueError('P3D v1 uses signed 16-bit vertex/polygon counts')
    wf(file, '<h', len(mesh.vertices))
    for v in mesh.vertices:
        wf(file, '<3f', float(v[0]), float(v[1]), float(v[2]))

    wf(file, '<h', len(mesh.polys))
    for poly in mesh.polys:
        wf(file, '<h2fh2fh2f',
           int(poly.p1), float(poly.u1), float(poly.v1),
           int(poly.p2), float(poly.u2), float(poly.v2),
           int(poly.p3), float(poly.u3), float(poly.v3))

    lights = getattr(model, 'v1_materials', None)
    if lights is None:
        lights = []
        for light in getattr(model, 'lights', []):
            lights.append({
                'position': light.pos,
                'range': light.range,
                'packed_color': light.color,
                'corona': int(light.show_corona),
                'lens_flares': int(light.show_lens_flares),
                'lightup': int(light.lightup_environment),
            })
    if len(lights) > 32767:
        raise ValueError('P3D v1 uses a signed 16-bit material/light count')
    wf(file, '<h', len(lights))
    for light in lights:
        pos = light['position']
        wf(file, '<4fIBBB', float(pos[0]), float(pos[1]), float(pos[2]),
           float(light['range']), int(light['packed_color']) & 0xFFFFFFFF,
           int(light['corona']) & 0xFF, int(light['lens_flares']) & 0xFF,
           int(light['lightup']) & 0xFF)


class P3D:
    def __init__(self):
        self.version = 2
        self.v1_header = P3D_V1_DEFAULT_HEADER
        self.v1_post_size_byte = 1
        self.v1_texture_groups = []
        self.v1_materials = []

        self.length = 0.0
        self.height = 0.0
        self.depth = 0.0

        self.num_textures = 0
        self.textures = []

        self.num_lights = 0
        self.lights = []

        self.num_meshes = 0
        self.meshes = []

        self.user_data_size = 0
        self.user_data = ''

    def __str__(self):
        print('\n{} textures:'.format(self.num_textures))
        for i in self.textures:
            print(i[0] if type(i) is tuple else i)

        print('\n{} lights:'.format(self.num_lights))
        for i in self.lights:
            print(i)
        
        print('\n{} meshes:'.format(self.num_meshes))
        for i in self.meshes:
            print(i)
        return 'model size: {:.2f} {:.2f} {:.2f}\n{} lights, {} meshes, {} textures\n'.format(
            self.length, self.height, self.depth, self.num_lights, 
            self.num_meshes, self.num_textures)

    def read(self, file):
        start = file.tell()
        signature = file.read(4)
        file.seek(start)
        if signature == b'P3D\x01':
            _read_v1(file, self)
            return
        if signature != b'P3D\x02':
            raise ValueError('Unknown P3D version/signature')
        self.version = 2

        def r(format):
            return rf(file, format)

        def r_str():
            return rf_str(file)
        
        # P3D2 signature
        file.read(4)

        self.length = r('<f')
        self.height = r('<f')
        self.depth = r('<f')

        # texture list
        # TEX + 4 bytes size signature
        file.read(7)
        self.num_textures = r('<B')
        for i in range(self.num_textures):
            tex_name = r_str()
            if tex_name.endswith('.tga'):
                tex_name = tex_name[0:-4]
            self.textures.append(tex_name)

        # lights list
        # LIGHTS + 4 bytes size signature
        file.read(10)

        self.num_lights = r('<H')
        for i in range(self.num_lights):
            p = Light()
            p.read(file)
            self.lights.append(p)

        # meshes list
        # MESHES + 4 bytes size signature
        file.read(10)
        self.num_meshes = r('<H')
        self.meshes = []
        for i in range(self.num_meshes):
            # SUBMESH + 4 bytes size signature
            file.read(11)
            p = Mesh()
            p.read(file, self.textures, self.num_textures)
            self.meshes.append(p)

        file.read(8)
        self.user_data_size = r('<i')

    def write(self, file):
        if getattr(self, 'version', 2) == 1:
            _write_v1(file, self)
            return

        # Write header
        file.write(b'P3D\x02')
        wf(file, '<3f', self.length, self.height, self.depth)

        # ---- TEX block ----
        def write_tex_data(buf):
            wf(buf, '<B', self.num_textures)
            for tex in self.textures:
                tn = tex + '.tga'
                wf_str(buf, tn.lower())
        write_block(file, b'TEX', write_tex_data)

        # ---- LIGHTS block ----
        def write_lights_data(buf):
            wf(buf, '<H', self.num_lights)
            for light in self.lights:
                light.write(buf)
        write_block(file, b'LIGHTS', write_lights_data)

        # ---- MESHES block ----
        def write_meshes_data(buf):
            wf(buf, '<H', self.num_meshes)
            for m in self.meshes:
                # Each mesh is written as a SUBMESH block
                def write_submesh_data(sub_buf):
                    m.write(sub_buf)
                write_block(buf, b'SUBMESH', write_submesh_data)
        write_block(file, b'MESHES', write_meshes_data)

        # ---- USER block ----
        def write_user_data(buf):
            wf(buf, '<i', 0)
        write_block(file, b'USER', write_user_data)