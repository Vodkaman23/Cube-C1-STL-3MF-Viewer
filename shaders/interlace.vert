#version 330

in vec2 in_position;
in vec2 in_texcoord;

out vec2 fragTexCoord;
out vec2 lcdCoord;

void main() {
    fragTexCoord = in_texcoord;
    // lcdCoord: top-down for LCD pixel mapping (row 0 = top of screen)
    lcdCoord = vec2(in_texcoord.x, 1.0 - in_texcoord.y);
    // Flip Y so readback is already top-down (avoids CPU-side np.flipud)
    gl_Position = vec4(in_position.x, -in_position.y, 0.0, 1.0);
}
