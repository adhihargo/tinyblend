"""
Tinyblend example 

The extern modules used (pyshaders, pyglbuffer) have been developped by myself to allow 3D games on pyglet.

PyGLbuffers: https://github.com/gabdube/pyglbuffers
Pyshaders: https://github.com/gabdube/pyshaders


"""

"""
MIT License

Copyright (c) 2016 Gabriel Dubé

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

import sys, math
from os.path import dirname as dn, abspath
sys.path.append(dn(dn(abspath(__file__))))

from pyglet.gl import GL_LINES, GL_FLOAT, GL_STATIC_DRAW, GL_UNSIGNED_SHORT
from pyglet.gl import glDrawElements, glClearColor, Config, glGenVertexArrays, glDeleteVertexArrays, glBindVertexArray, GLuint, glViewport
from pyglet.window import Window, mouse, get_platform   
from pyglet import app

import tinyblend as blend
import pyshaders as shaders
from pyglbuffers import Buffer
from matmath import translate, perspective, rotate

# Load the bindings in order to operate more easily with pyglbuffers
shaders.load_extension('pyglbuffers_bindings')

class Game(Window):

    def __init__(self):
        display = get_platform().get_default_display()
        screen = display.get_default_screen()

        template = Config(alpha_size=8)
        config = screen.get_best_config(template)
        config.major_version = 3
        config.minor_version = 3
        context = config.create_context(None)
        
        Window.__init__(self, 800, 600, visible=False, resizable=True, caption='Tinyblend example', context=context)
        
        # Most assets are located in the blend file
        self.assets = blend.BlenderFile('_assets.blend')

        self.vao = (GLuint*1)()
        glGenVertexArrays(1, self.vao)
        glBindVertexArray(self.vao[0])

        # Load shaders
        shader = shaders.from_files_names('shaders/main.glsl.vert', 'shaders/main.glsl.frag')
        shader.owned = False
        shader.use()
        shader.enable_all_attributes()
        self.shader = shader

        # Uniforms matrices setup
        self.rotation = [-90,0,0]
        self.position = [0,0,-4.5]
        shaders.transpose_matrices(False)
        self.upload_uniforms()

        # Scene creation
        self.setup_scene()

        # Show the window
        self.set_visible()

    def setup_scene(self):
        " Load the assets in the scene "

        # Get the suzanne object from the blend file
        objects = self.assets.find('Object')
        bsuzanne = objects.find_by_name('Suzanne')

        # Get the vertices data of the suzanne object
        suz_data = bsuzanne.data
        vertices, indices = [], []
        
        for v in suz_data.mvert:
            vertices.append(v.co)

        for edge in suz_data.medge:
            indices.append((edge.v1, edge.v2))

        # Pack the vertices data of the suzanne object to be used by opengl
        suzanne = Buffer.array('(3f)[position]', GL_STATIC_DRAW)
        suzanne_indices = Buffer.element('(2S)[elem]', GL_STATIC_DRAW)
        suzanne.init(vertices)
        suzanne_indices.init(indices)
        self.suzanne = (suzanne, suzanne_indices, len(suzanne_indices)*2)

        self.shader.map_attributes(suzanne)

        # Set the background color
        glClearColor(0.1, 0.1, 0.1, 1.0)

    def upload_uniforms(self):
        width, height = self.get_size()

        uni = self.shader.uniforms
        
        uni.view = translate(None, tuple(self.position) )

        mod_mat = rotate(None, self.rotation[0], (1.0, 0.0, 0.0))
        mod_mat = rotate(mod_mat, self.rotation[1], (0.0, 1.0, 0.0))
        uni.model = rotate(mod_mat, self.rotation[2], (0.0, 0.0, 1.0)) 

        uni.proj = perspective(60.0, width/height, 0.1, 256.0)

    def on_resize(self, width, height):
        glViewport(0,0, width, height)
        self.upload_uniforms()

    def on_mouse_scroll(self, x, y, scroll_x, scroll_y):
        self.position[2] -= 0.3*scroll_y
        self.upload_uniforms()

    def on_mouse_drag(self, x, y, dx, dy, buttons, modifiers):
        if buttons & mouse.LEFT != 0:
            self.rotation[0] += dy * 1.25
            self.rotation[1] += dx * 1.25
        elif buttons & mouse.RIGHT != 0:
            self.position[0] += dx * 0.005
            self.position[1] += dy * 0.005

        self.upload_uniforms()

    def on_draw(self):
        # Clear the window
        self.clear()

        # Draw the mesh
        suz, suz_indices, suz_len = self.suzanne
        suz.bind()
        suz_indices.bind()
        
        glDrawElements(GL_LINES, suz_len, GL_UNSIGNED_SHORT, 0)
    
def main():
    game = Game()
    app.run()

if __name__ == '__main__':
    main()

