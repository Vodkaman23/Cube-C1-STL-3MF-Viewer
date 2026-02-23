#version 330

// Vertex shader for STL/3MF mesh rendering
// Passes world-space and eye-space data to fragment shader
// Supports optional per-vertex color from 3MF files

in vec3 in_position;
in vec3 in_normal;
in vec3 in_color;  // per-vertex color (white if no vertex colors)

out vec3 frag_normal;
out vec3 frag_position;
out vec3 frag_eye_pos;   // camera position in world space (for specular)
out vec3 frag_color;     // per-vertex color

uniform mat4 model;
uniform mat4 view;
uniform mat4 projection;

void main() {
    vec4 world_position = model * vec4(in_position, 1.0);
    frag_position = world_position.xyz;

    // Normal matrix handles non-uniform scaling
    mat3 normal_matrix = mat3(transpose(inverse(model)));
    frag_normal = normalize(normal_matrix * in_normal);

    // Extract camera world position from inverse view matrix
    mat4 inv_view = inverse(view);
    frag_eye_pos = inv_view[3].xyz;

    frag_color = in_color;

    gl_Position = projection * view * world_position;
}
