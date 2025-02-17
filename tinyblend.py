# -*- coding: utf-8 -*-
# MIT License
#
# Copyright (c) 2016 Gabriel Dubé
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

import re
from collections import namedtuple
from enum import Enum
from io import SEEK_CUR, SEEK_END, SEEK_SET
from struct import Struct
from weakref import ref

"""
Assets loader for the blender file format (.blend).

Usage:

```python
from tinyblend import BlendFile

blend_file = BlendFile('myblend.blend')
scenes = blend_file.list('Scene')

for scene in scenes:
    print(scene.id.name)

# Dont know the fields names of a certain blender struct, no problem:
print(blend_file.tree('World'))

```

Author: Gabriel Dube
"""

# List of base types found in blend fields and their struct char representation.
_BASE_TYPES = {'float': 'f', 'double': 'd', 'int': 'i', 'short': 'h', 'ushort': "H", 'char': 'c',
               'long': 'l', 'ulong': 'L', 'uint64_t': 'Q', 'int64_t': 'q'}

BlendFileArch = Enum('BlendFileArch', (('X32', 'I'), ('X64', 'Q')), qualname='BlendFileArch')
BlendFileEndian = Enum('BlendFileEndian', (('Little', '<'), ('Big', '>')), qualname='BlendFileEndian')

# Version structures
BlendVersionInfo = namedtuple('BlendVersionInfo', ('major', 'minor', 'rev'))
BlendFileHeader = namedtuple('BlendFileInfo', ('version', 'arch', 'endian'))

# Index structures
BlendBlockHeader = namedtuple('BlendBlockHeader', ('code', 'size', 'addr', 'sdna', 'count'))
BlendStructFieldDNA = namedtuple('BlendStructFieldDNA', ('index_type', 'index_name'))
BlendStructDNA = namedtuple('BlendStructDNA', ('index', 'fields'))
BlendStructField = namedtuple('BlendStructField', ('name', 'type', 'size', 'ptr', 'count'))
BlendStruct = namedtuple('BlendStruct', ('name', 'fields'))
BlendIndex = namedtuple('BlendIndex', ('field_names', 'type_names', 'type_sizes', 'structures'))


class BlenderFileException(Exception):
    """
        Base exception class for blender import related exceptions

        Author: Gabriel Dube
    """

    def __init__(self, message):
        self.message = message

    def __repr__(self):
        return "Error while executing action on blender file: {}".format(repr(self.message))


class BlenderFileImportException(BlenderFileException):
    """
        Exception raised when a blender file import fails.
        
        Author: Gabriel Dube
    """

    def __init__(self, message):
        super().__init__(message)


class BlenderFileReadException(BlenderFileException):
    """
        Exception raised when reading bad values from a blend file

        Author: Gabriel Dube
    """

    def __init__(self, message):
        super().__init__(message)


class NamedStruct(object):
    """
        A type that fuse namedtuple and Struct together.
    """

    __fields__ = ('names', 'format')

    def __init__(self, name, fmt, *fields):
        self.format = Struct(fmt)
        self.names = namedtuple(name, fields)

    @classmethod
    def from_namedtuple(cls, ntuple, fmt):
        """
            Build a NamedStruct from a namedtuple

            Author: Gabriel Dube
        """
        named_struct = super(NamedStruct, cls).__new__(cls)
        named_struct.format = Struct(fmt)
        named_struct.names = ntuple

        return named_struct

    def unpack(self, data):
        x, y = len(data), self.format.size
        if (x > y) and (x % y == 0):
            return tuple(self.iter_unpack(data))

        return self.names(*self.format.unpack(data))

    def unpack_from(self, data, offset):
        return self.names(*self.format.unpack_from(data, offset))

    def iter_unpack(self, data):
        return (self.names(*d) for d in self.format.iter_unpack(data))


class AddressLookup(object):
    """
        Descriptor that wraps get/set actions on pointer fields.
    """
    __slots__ = ['name', 'value']

    def __init__(self, name):
        # ptr is the suffix given to all pointer fields in compile_fmt
        self.name = 'ptr_' + name
        self.value = None

    def __set__(self, instance, value):
        raise AttributeError('Attribute cannot be setted')

    def __get__(self, instance, cls):
        ptr = getattr(instance, self.name)
        if type(ptr) is int and ptr != 0:
            self.value = instance.file._from_address(ptr)
        elif type(ptr) is tuple:
            self.value = instance.file._from_addresses(ptr)

        return self.value

    def __delete__(self, instance):
        raise AttributeError('Attribute cannot be deleted')


class BlenderObject(object):
    """
        Blender object base. Unpack raw data depending on the subclass format string.

        Class names:
            VERSION: Version of the blend file for this struct. (Blender structures change with blender versions)

        Instance names:
            file: The parent file of this object

        Pointer Fields:
            In order to read pointer fields, a lookup in the blend file is neccesary.
            This class keeps a weakref to to its parent blend file. If the parent file
            is closed or freed, the pointer lookup will raise a RuntimeError

        Author: Gabriel Dube
    """
    # Cache for BlenderObject subclasses. Dict of {VERSION: {CLASS_NAME: CLASS}}
    CACHE = {}

    # Version of the blend file. Overriden in subclasses.
    VERSION = None

    # Format Struct to unpack the raw data. Overriden in subclasses
    FMT = None

    # List of (subclasses, offset) in the object. Overriden is subclasses 
    CLASSES = None

    def __new__(cls, file, data):
        obj = super(BlenderObject, cls).__new__(cls)
        obj._file = file

        # Unpack structures from the raw data to the object
        for cls_, slice_, name in cls.CLASSES:
            obj_ = cls_(file, data[slice_])
            setattr(obj, name, obj_)

        try:
            BlenderObject._set_fields(cls.FMT.names._fields, cls.FMT.unpack(data), obj)
        except StopIteration:
            pass

        return obj

    def __eq__(self, other):
        if type(other) is not type(self):
            return False

        eq = False
        template = re.compile(r'(.+)_\d+_(\d+)')
        name_iter = iter(self.FMT.names._fields)
        for name in name_iter:
            match = template.findall(name)
            if len(match) == 0:
                eq |= getattr(self, name) == getattr(other, name)
            else:
                field_name, array_len = match[0]
                eq |= getattr(self, field_name) == getattr(other, field_name)

                # Skip the following names in the array
                [next(name_iter) for _ in range(int(array_len) - 1)]

        return eq

    @staticmethod
    def _set_fields(names, data, obj):
        """
            Unpack the base types (float, int, etc) from the raw data to the object

            Author: Gabriel Dube
        """
        template = re.compile(r'(.+)_\d+_(\d+)')
        gen = zip(names, data)
        while True:
            name, value = gen.__next__()
            match = template.findall(name)
            if len(match) == 0:
                setattr(obj, name, value)
            else:
                field_name, array_len = match[0]
                arr = [value]
                for i in range(int(array_len) - 1):
                    _, value = gen.__next__()
                    arr.append(value)
                setattr(obj, field_name, tuple(arr))

    @property
    def file(self):
        file = self._file()
        if file is None:
            raise RuntimeError('Parent blend file was freed')

        return file

    def tree(self, recursive=True, max_level=999):
        return self.file.tree(type(self).__name__, recursive, max_level)


class BlenderObjectFactory(object):
    """
        Object that reads blender structures from datablocks. A BlenderObjectFactory
        is created when the data types is accessed for the first time in a blend file.
        The BlenderObjectFactory is then cached because their creation can be quite expensive.

        A BlenderObjectFactory keeps a weakref to its parent blend file. If the parent file
        is closed or freed, all its methods will raise a RuntimeError

        The BlenderObjectFactory creates a new type from the blender structure definition,
        this type is then used to unpack the blender objects from raw data. For more information
        see the BlenderObject documentation.

        Author: Gabriel Dube
    """
    # Cache for instanced factories. Dict of {VERSION: {CLASS_NAME: CLASS}}
    CACHE = {}

    def __init__(self, file, type_name_index):
        self._file_ref = ref(file)

        for index, struct_dna in enumerate(file.index.structures):
            if struct_dna.index == type_name_index:
                self.struct_dna = struct_dna
                self.sdna_index = index
                break

        dnafields = self.struct_dna.fields
        dnatypes = file.index.type_names

        # A type has a name if it has a ID type (always the first parameter)
        self.has_name = 'ID' in (dnatypes[ftype] for ftype, fname in dnafields)

        self.object_type, _ = self._build_object_type(file, self.struct_dna)
        self.object_name = file.index.type_names[self.struct_dna.index]

    def __len__(self):
        file = self.file
        blocks = file.blocks
        count = 0

        for block, offset in blocks:
            count += block.sdna == self.sdna_index

        return count

    def __repr__(self):
        file = self.file

        dna = self.struct_dna
        return "<BlenderObjectFactory for '{}' objects>".format(file.index.type_names[dna.index])

    def __iter__(self):
        file = self.file
        blocks = file.blocks

        block: BlendBlockHeader
        for block, offset in blocks:
            if block.sdna == self.sdna_index:
                data = file._read_block(block, offset)
                yield self.object_type(self._file_ref, data)

    @staticmethod
    def compile_fmt(fields):
        """
            Compile a list of BlenderFile.BlendStructField into a format string that can be passed
            to a Struct objet

            Author: Gabriel Dube
        """
        base_types = _BASE_TYPES
        fmt = ''
        fmt_names = []

        # names cannot start with an underscore
        def fix_name(n):
            if n.startswith("_"):
                return "f" + n
            else:
                return n

        for f in fields:
            t, count = f.type, str(f.count)

            if f.count > 1 and t != 'char':
                # A unique name must be generated for every item in an array that is not composed of chars
                # This is translated to a python list when the blender types is instanciated
                name = (fix_name(f.name + ('_{}_{}'.format(i, count))) for i in range(f.count))
            else:
                name = (fix_name(f.name),)

            # If type is a pointer
            if f.ptr:
                fmt_names.extend(('ptr_' + n for n in name))
                fmt += count + 'P'
                continue

            # If type is a base type
            if t in base_types:
                # Strings
                if t == 'char' and f.count > 1:
                    t = count + 's'
                else:
                    t = count + base_types[t]
                fmt_names.extend(name)
                fmt += t
                continue

            # If type is another structure
            fmt += (str(f.size) + 'x')

        return fmt, fmt_names

    @staticmethod
    def _build_object_type(blend_file, struct_dna):
        """
            Dynamically create Python object type from Blender file SDNA for the factory.

            Author: Gabriel Dube
        """
        head = blend_file.header
        arch, endian = head[1::]

        # Get cache
        version = blend_file.header.version
        version_cache = BlenderObject.CACHE.get(version)
        if version_cache is None:
            version_cache = {}
            BlenderObject.CACHE[version] = version_cache

        # Get the name of the struct
        name = blend_file.index.type_names[struct_dna.index]

        # If type was cached, use the cached version
        object_type = version_cache.get(name, type(None))()
        if object_type is not None:
            return object_type, object_type.CLASSES

        # If type was not cached, create a new blender object type
        dependencies = []
        offset = 0

        # 1. Parse the raw fields data
        name, fields = blend_file._export_struct(struct_dna)

        # 2. Extract other blender objects types contained in this object (pointer fields types are ignored)
        #    The dependency contains the type, a slice to extract the child data from the parent data and the name to be
        #    used in the parent object
        for f, dna in zip(fields, struct_dna.fields):
            if f.type not in _BASE_TYPES and not f.ptr:
                tmp_dna = blend_file._struct_lookup(dna.index_type)
                dep = (BlenderObjectFactory._build_object_type(blend_file, tmp_dna)[0],
                       slice(offset, offset + f.size),
                       f.name)
                dependencies.append(dep)

            offset += f.size

        # 3. Compile a format string from the extracted fields and build a namedstruct to extract the raw data. See the
        #    BlenderObject constructor.
        fmt, fmt_names = BlenderObjectFactory.compile_fmt(fields)
        fmt_names = namedtuple(name, fmt_names, rename=True)
        fmt = endian.value + fmt.replace('P', arch.value)
        fmt = NamedStruct.from_namedtuple(fmt_names, fmt)

        # 4. Then build the type itself
        class_attrs = {'VERSION': version, 'FMT': fmt, 'CLASSES': dependencies}

        # Add pointer lookup descriptor to the type attributes
        for f in (f for f in fields if f.ptr):
            class_attrs[f.name] = AddressLookup(f.name)

        object_type = type(name, (BlenderObject,), class_attrs)
        version_cache[name] = ref(object_type)

        return object_type, tuple(dependencies)

    @property
    def file(self):
        file: BlenderFile = self._file_ref()
        if file is None:
            raise RuntimeError('Parent blend file was freed')

        return file

    def find_by_name(self, name):
        """
            Find and build an object by name. If the object does not have a name,
            raise a BlenderFileReadException.

            Author: Gabriel Dube
        """
        if not self.has_name:
            raise BlenderFileReadException('Object type do not have a name')

        name_bytes = name.encode()
        for obj in self:
            _name = obj.id.name
            if _name[2:_name.index(0)] == name_bytes:
                return obj

        raise KeyError('File do not have {} objects named \'{}\''.format(self.object_name, name))


class BlenderFile(object):
    """
        Parse a blender file and extract the contained assets

        Attributes:
            * handle - Underlying file object
            * header - Information about the whole file. version (major,minor,rev), arch (32/64bits) and endianess (little/big)
            * blocks - List of blender data block in the blend file
            * index  - List if all name, types and structures contained in the blend file
    
        Author: Gabriel Dube
    """

    def __init__(self, blend_file_name):
        handle = self._get_file_handler(blend_file_name)
        header = self._parse_header(handle.read(12))
        if header is None:
            raise BlenderFileImportException('Bad file header')

        self.header = header
        self.handle = handle
        self.blocks, self.index = self._parse_blocks()

        if BlenderObjectFactory.CACHE.get(header.version) is None:
            BlenderObjectFactory.CACHE[header.version] = {}

    @staticmethod
    def _parse_header(header) -> BlendFileHeader:
        """
            Parse the header of a blender file and return the formatted data in a BlendFileInfo object.
            The passed header must be valid.

            Author: Gabriel Dube
            :param header: The header to parse as a byte string
            :return: A BlendFileInfo object if successful, None otherwise.
        """
        if len(header) != 12 or header[0:7] != b'BLENDER':
            return None

        arch = header[7:8]
        endian = header[8:9]
        version = [x - 48 for x in header[9::]]

        if arch == b'-':
            arch = BlendFileArch.X64
        elif arch == b'_':
            arch = BlendFileArch.X32
        else:
            return None

        if endian == b'v':
            endian = BlendFileEndian.Little
        elif endian == b'V':
            endian = BlendFileEndian.Big
        else:
            return None

        version = BlendVersionInfo(*version)

        return BlendFileHeader(version=version, arch=arch, endian=endian)

    def _struct_lookup(self, index):
        """
            Lookup for a struct definition in the blender file.
            Raise a BlenderFileReadException if index is not associated with a struct.

            Author: Gabriel Dube
        """

        lookup = (s for s in self.index.structures if s.index == index)
        try:
            return next(lookup)
        except StopIteration:
            if index > len(self.index.type_names) or index < 0:
                msg = 'Type index {} is not valid for this blend file'.format(index)
            else:
                type_name = self.index.type_names[index]
                msg = 'Type {} is not associated with a structure'.format(type_name)

            raise BlenderFileReadException(msg)

    def _export_struct(self, struct):
        """
            Format a blender struct object fields in a human readable dict.
            This is used when creating blender object types

            If recursive is set to True, the function will also export the composed types of struct

            Author: Gabriel Dube
        """
        field_names = self.index.field_names
        type_names = self.index.type_names
        type_sizes = self.index.type_sizes

        ptr_size = 8 if self.header.arch == BlendFileArch.X64 else 4

        struct_name = type_names[struct.index]
        struct_fields = []

        array_template = re.compile(r'\[(\d+)\]')

        for ftype, fname in struct.fields:
            name = field_names[fname]
            _type = type_names[ftype]
            size = type_sizes[ftype]

            is_ptr = name[0] == '*'
            is_array = name[-1] == ']'

            if is_ptr:
                name = name.lstrip('*')
                size = ptr_size

            if is_array:
                count = 1
                for v in array_template.findall(name):
                    count *= int(v)
                name = name[0:name.index('[')]
                size *= count
            else:
                count = 1

            struct_fields.append(BlendStructField(name=name, type=_type, size=size, ptr=is_ptr, count=count))

        return BlendStruct(name=struct_name, fields=tuple(struct_fields))

    def _fmt_strct(self, fmt):
        """
            Format a Struct format string to match the blender file endianess and pointer sizes.

            Author: Gabriel Dube
        """
        head = self.header
        return head.endian.value + (fmt.replace('P', head.arch.value))

    def _parse_index(self, head) -> BlendIndex:
        """
            Parse the blender index and return the parsed data.
            The index has an unknown length, so it cannot be parsed in one shot

            Author: Gabriel Dube
        """

        def align():
            nonlocal offset
            tmp = offset % 4
            offset += 0 if tmp == 0 else 4 - tmp

        Int = NamedStruct('Int', self._fmt_strct('i'), 'val')
        Short = NamedStruct('Short', self._fmt_strct('h'), 'val')
        StructFieldDNA = NamedStruct.from_namedtuple(BlendStructFieldDNA, self._fmt_strct('hh'))
        StructDNA = BlendStructDNA

        rewind_offset = self.handle.seek(0, SEEK_CUR)
        data = self.handle.read(head.size)

        # Reading the blend field names
        offset = 8
        if data[0:8] != b'SDNANAME':
            raise BlenderFileImportException('Malformed index')

        name_count = Int.unpack_from(data, offset).val
        field_names = [n.decode('utf-8') for n in data[offset + 4::].split(b'\x00', name_count)[:-1]]

        # Reading the blend field types
        offset += sum((len(n) for n in field_names)) + len(field_names) + 4
        align()  # Size of all names + size of all null char + name_count offset and Align the offset at 4 bytes
        if data[offset:offset + 4] != b'TYPE':
            raise BlenderFileImportException('Malformed index')

        type_count = Int.unpack_from(data, offset + 4).val
        type_names = [n.decode('utf-8') for n in data[offset + 8::].split(b'\x00', type_count)[:-1]]

        # Reading the types length
        offset += sum((len(t) for t in type_names)) + len(type_names) + 8
        align()
        if data[offset:offset + 4] != b'TLEN':
            raise BlenderFileImportException('Malformed index')

        offset += 4
        type_data_length = Short.format.size * type_count
        type_sizes = (x.val for x in Short.iter_unpack(data[offset:offset + type_data_length]))

        # Reading structures information
        offset += type_data_length
        align()
        if data[offset:offset + 4] != b'STRC':
            raise BlenderFileImportException('Malformed index')

        offset += 8
        structures = []
        structure_count = Int.unpack_from(data, offset - 4).val
        for _ in range(structure_count):
            structure_type_index = Short.unpack_from(data, offset).val

            field_count = Short.unpack_from(data, offset + 2).val
            fields = []
            offset += 4
            for _ in range(field_count):
                fields.append(StructFieldDNA.unpack_from(data, offset))
                offset += 4

            structures.append(StructDNA(index=structure_type_index, fields=tuple(fields)))

        # Rewind the blend at the end of the block head
        self.handle.seek(rewind_offset, SEEK_SET)

        return BlendIndex(
            field_names=tuple(field_names),
            type_names=tuple(type_names),
            type_sizes=tuple(type_sizes),
            structures=tuple(structures)
        )

    def _parse_blocks(self) -> ([(BlendBlockHeader, int)], BlendIndex):
        """
            Extract the file block headers from the file. 
            Return a list of extracted blocks and the index (aka SDNA) block

            Author: Gabriel Dube
            :return: A tuple consisting of:
                - List of tuple (block header and its position)
                - Index of the file's structures
        """
        handle = self.handle
        BlockHeader = NamedStruct.from_namedtuple(BlendBlockHeader, self._fmt_strct('4siPii'))
        header_block_size = BlockHeader.format.size

        # Get the blend file size
        end = handle.seek(0, SEEK_END)
        handle.seek(12, SEEK_SET)

        blend_index = None
        end_found = False
        block_head_offset_pairs = []
        while end != handle.seek(0, SEEK_CUR) and not end_found:
            buf = handle.read(header_block_size)
            block_head: BlendBlockHeader = BlockHeader.unpack(buf)

            # DNA1 indicates the index block of the blend file
            # ENDB indicates the end of the blend file
            if block_head.code == b'DNA1':
                blend_index = self._parse_index(block_head)
            elif block_head.code == b'ENDB':
                end_found = True
            else:
                block_head_offset_pairs.append((block_head, handle.seek(0, SEEK_CUR)))

            handle.seek(block_head.size, SEEK_CUR)

        if blend_index is None:
            raise BlenderFileImportException('Could not find blend file index')

        if not end_found:
            raise BlenderFileImportException('End of the blend file was not found')

        return tuple(block_head_offset_pairs), blend_index

    def _read_block(self, block, offset):
        """
            Read a block data and return it.

            Author: Gabriel Dube
        """
        handle = self.handle
        handle.seek(offset, SEEK_SET)
        data = handle.read(block.size)
        return data

    def _from_address(self, ptr):
        """
            Extract data from its old memory address. This is used in pointer fields lookup.
            If the pointer cannot be found raise a BlenderFileReadException

            Author: Gabriel Dube
        """
        block, offset = None, None
        for _block, _offset in self.blocks:
            if _block.addr == ptr:
                block, offset = _block, _offset

        if offset is None:
            raise BlenderFileReadException('Cannot find the address {} in the blend file'.format(hex(ptr)))

        struct_dna = self.index.structures[block.sdna]
        block_data = self._read_block(block, offset)
        obj, _ = BlenderObjectFactory._build_object_type(self, struct_dna)
        if block.count == 1:
            return obj(ref(self), block_data)
        else:
            ref_self = ref(self)
            step = obj.FMT.format.size
            return tuple([obj(ref_self, block_data[x:x + step]) for x in range(0, block.size, step)])

    def _from_addresses(self, ptr_list):
        return [self._from_address(ptr) if ptr != 0 else None for ptr in ptr_list]

    @staticmethod
    def _get_file_handler(path):
        return open(path, 'rb')

    def list(self, type_name):
        """
            Creates or get a cached version of a blender type factory. A BlenderObjectFactory
            offers a pythonic interface to read blend file data of a certain type. For more information see
            BlenderObjectFactory

            Author: Gabriel Dube
            :param type_name: Name of the data type to load. Ex: 'Scene'
            :return:
        """
        version = self.header.version
        factories: dict = BlenderObjectFactory.CACHE.get(version)

        # Check if the factory was already created
        fact = factories.get(type_name, type(None))()
        if fact is None:
            # Factory creation
            try:
                fact = BlenderObjectFactory(self, self.index.type_names.index(type_name))
                factories[type_name] = ref(fact)
            except ValueError:
                raise BlenderFileReadException('Data type {} could not be found in the blend file'.format(type_name))
        return fact

    def tree(self, type_name, recursive=True, max_level=999):
        """
            Return a representation of the struct. Useful when looking for attributes in a struct

            Author: Gabriel Dube
        """

        def field_lookup(struct, indent_level=0):
            indent = '    ' * indent_level
            fields = ''
            for ftype, fname in struct.fields:
                _type_name, _field_name = type_names[ftype], field_names[fname]
                fields += indent + '|-- {} {}'.format(_type_name, _field_name) + '\n'
                if recursive and indent_level < max_level:
                    if ftype in struct_indexes and '*' not in _field_name:
                        fields += field_lookup(self._struct_lookup(ftype), indent_level + 1)

            return fields

        field_names = self.index.field_names
        type_names = self.index.type_names
        struct_indexes = [s.index for s in self.index.structures]

        type_index = type_names.index(type_name)
        dna = self._struct_lookup(type_index)

        repr_str = '{} ({})\n'.format(type_name, self.header.version)
        repr_str += field_lookup(dna)

        return repr_str

    def list_structures(self):
        names = []
        types = self.index.type_names
        for s in self.index.structures:
            names.append(types[s.index])

        return sorted(names)

    def close(self):
        self.handle.close()
