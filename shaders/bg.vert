#version 330

// Fullscreen quad vertex shader for background
in vec2 in_position;

out vec2 v_uv;  // normalized position (0..1)

void main() {
    v_uv = in_position * 0.5 + 0.5;
    gl_Position = vec4(in_position, 0.9999, 1.0);  // behind everything
}
