# -*- coding: utf-8 -*-
"""
Assets loader tests

author: Gabriel Dube
"""
import gc
import sys
from os.path import dirname as dn

import pytest

sys.path.append(dn(dn(__file__)))
from tinyblend import BlendFileEndian, BlenderFile, BlendFileArch, BlenderFileReadException, BlenderObjectFactory, \
    BlenderFileImportException, BlenderObject


def test_open_blend_file():
    blend = BlenderFile('fixtures/test1.blend')

    head = blend.header
    assert 'VersionInfo(major=2, minor=7, rev=7)', repr(head.version)
    assert BlendFileArch.X64, head.arch
    assert BlendFileEndian.Little, head.endian

    blend.close()


def test_open_blend_file_28():
    blend = BlenderFile('fixtures/test_blender28.blend')

    head = blend.header
    assert 'VersionInfo(major=2, minor=8, rev=0)', repr(head.version)
    assert BlendFileArch.X64, head.arch
    assert BlendFileEndian.Little, head.endian

    blend.close()


def test_should_read_scene_data():
    blend = BlenderFile('fixtures/test1.blend')

    worlds = blend.list('World')
    assert worlds.file is blend, 'World file is not blend'
    assert len(worlds) == 1, 'Test blend should have one world'

    world = worlds.find_by_name('TestWorld')
    assert world.file is blend
    assert isinstance(world, worlds.object_type)
    assert world.VERSION == blend.header.version
    assert len(world.mtex) == 18
    assert 12.8999 < world.aodist < 12.90001
    assert world.id.name[0:11] == b'WOTestWorld'

    scenes = blend.list('Scene')
    assert len(scenes) == 1, 'Test blend should have one scene'

    rctfs = blend.list('rctf')
    pytest.raises(BlenderFileReadException, rctfs.find_by_name, 'blah')  # rctf object do not have a name
    pytest.raises(BlenderFileReadException, blend.list, 'foos')  # foos is not a valid structure
    pytest.raises(KeyError, worlds.find_by_name, 'BOO')  # There are no worlds by the name of BOO in the blend file

    blend.close()


def test_should_read_scene_data_28():
    blend = BlenderFile('fixtures/test_blender28.blend')

    worlds = blend.list('World')
    assert worlds.file is blend, 'World file is not blend'
    assert len(worlds) == 1, 'Test blend should have one world'

    scenes = blend.list('Scene')
    assert len(scenes) == 1, 'Test blend should have one scene'

    blend.close()


def test_equality():
    blend = BlenderFile('fixtures/test1.blend')

    worlds = blend.list('World')

    world1 = worlds.find_by_name('TestWorld')
    world2 = worlds.find_by_name('TestWorld')

    assert id(world1) is not id(world2)
    assert world1 == world2


def test_should_lookup_pointer():
    BlenderObject.CACHE = {}
    BlenderObjectFactory.CACHE = {}

    blend = BlenderFile('fixtures/test1.blend')

    worlds = blend.list('World')
    scenes = blend.list('Scene')

    world = worlds.find_by_name('TestWorld')
    scene = scenes.find_by_name('MyTestScene')

    pytest.raises(BlenderFileReadException, blend._from_address, 0)
    pytest.raises(AttributeError, setattr, scene, 'world', 0)
    pytest.raises(AttributeError, delattr, scene, 'world')

    scene_world = scene.world

    assert type(scene_world) is worlds.object_type
    assert scene_world is not world
    assert scene.world == world
    assert scene.world is scene_world
    assert scene.id.next is None  # Null pointer lookup returns None


def test_should_lookup_pointer_array():
    blend = BlenderFile('fixtures/test1.blend')

    obj = blend.list('Object').find_by_name('Suzanne')
    data = obj.data

    assert data.totvert == len(data.mvert)


def test_blend_struct_lookup():
    blend = BlenderFile('fixtures/test1.blend')

    scene_index = blend.index.type_names.index('Scene')
    float_index = blend.index.type_names.index('float')
    bad_index = 983742

    struct = blend._struct_lookup(scene_index)
    assert struct.index == scene_index, 'Struct index is not scene index'

    pytest.raises(BlenderFileReadException, blend._struct_lookup, float_index)
    pytest.raises(BlenderFileReadException, blend._struct_lookup, 983742)

    blend.close()


def test_weakref():
    blend = BlenderFile('fixtures/test1.blend')
    worlds = blend.list('World')

    del blend

    pytest.raises(RuntimeError, getattr, worlds, 'file')
    pytest.raises(RuntimeError, len, worlds)
    pytest.raises(RuntimeError, repr, worlds)
    pytest.raises(RuntimeError, str, worlds)
    pytest.raises(RuntimeError, worlds.find_by_name, '...')


def test_cache_lookup():
    blend = BlenderFile('fixtures/test1.blend')
    v = blend.header.version

    worlds = blend.list('World')

    assert BlenderObjectFactory.CACHE[v]['World']() is not None
    assert BlenderObject.CACHE[v]['World']() is not None

    del worlds
    gc.collect()

    assert BlenderObjectFactory.CACHE[v]['World']() is None
    assert BlenderObject.CACHE[v]['World']() is None

    worlds = blend.list('World')
    assert isinstance(worlds, BlenderObjectFactory)
    assert BlenderObjectFactory.CACHE[v]['World']() is not None
    assert BlenderObject.CACHE[v]['World']() is not None

    blend.close()


def test_list_structures():
    blend = BlenderFile('fixtures/test1.blend')
    structs = blend.list_structures()
    assert len(structs) > 30
    assert 'Scene' in structs


def test_tree():
    blend = BlenderFile('fixtures/test1.blend')
    scene_repr = blend.tree('Scene')
    assert len(scene_repr.splitlines()) > 100
    assert 'ID' in scene_repr


def test_open_bad_blend_file():
    pytest.raises(BlenderFileImportException, BlenderFile, 'fixtures/test2.blend')
    pytest.raises(BlenderFileImportException, BlenderFile, 'fixtures/test3.blend')
