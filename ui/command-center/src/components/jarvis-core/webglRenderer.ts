import type { JarvisCoreVisualState } from "../../lib/jarvisCoreState";

type Palette = {
  inner: [number, number, number];
  outer: [number, number, number];
  ring: [number, number, number];
  pulse: number;
  spin: number;
};

const PALETTES: Record<JarvisCoreVisualState, Palette> = {
  HEALTHY_IDLE: {
    inner: [0.54, 1, 0.94],
    outer: [0.2, 0.55, 0.72],
    ring: [0.4, 0.85, 1],
    pulse: 0.35,
    spin: 0.18,
  },
  ACTIVE_MISSION: {
    inner: [0.45, 1, 0.88],
    outer: [0.1, 0.72, 0.62],
    ring: [0.2, 0.95, 0.82],
    pulse: 0.75,
    spin: 0.55,
  },
  VERIFYING: {
    inner: [1, 0.78, 0.35],
    outer: [0.72, 0.48, 0.08],
    ring: [1, 0.62, 0.2],
    pulse: 0.62,
    spin: 0.32,
  },
  DEGRADED: {
    inner: [1, 0.62, 0.28],
    outer: [0.62, 0.28, 0.08],
    ring: [0.95, 0.45, 0.15],
    pulse: 0.48,
    spin: 0.22,
  },
  STOPPED: {
    inner: [1, 0.35, 0.55],
    outer: [0.55, 0.08, 0.22],
    ring: [0.95, 0.2, 0.42],
    pulse: 0.28,
    spin: 0.08,
  },
  DISCONNECTED: {
    inner: [0.45, 0.52, 0.62],
    outer: [0.12, 0.16, 0.22],
    ring: [0.32, 0.38, 0.48],
    pulse: 0.12,
    spin: 0.04,
  },
};

const VERT = `
attribute vec2 a_position;
void main() {
  gl_Position = vec4(a_position, 0.0, 1.0);
}
`;

const FRAG = `
precision mediump float;
uniform vec2 u_resolution;
uniform float u_time;
uniform vec3 u_inner;
uniform vec3 u_outer;
uniform vec3 u_ring;
uniform float u_pulse;
uniform float u_spin;

void main() {
  vec2 uv = (gl_FragCoord.xy / u_resolution.xy) * 2.0 - 1.0;
  uv.x *= u_resolution.x / u_resolution.y;
  float dist = length(uv);
  float angle = atan(uv.y, uv.x) + u_time * u_spin;
  float ring = smoothstep(0.58, 0.62, dist) * (1.0 - smoothstep(0.68, 0.72, dist));
  float core = 1.0 - smoothstep(0.0, 0.42 + sin(u_time * 2.2) * 0.04 * u_pulse, dist);
  float orbit = smoothstep(0.34, 0.36, dist) * (1.0 - smoothstep(0.38, 0.4, dist));
  float sweep = 0.5 + 0.5 * sin(angle * 3.0 + u_time * 1.4);
  vec3 color = mix(u_outer, u_inner, core);
  color += u_ring * ring * (0.55 + 0.45 * sweep);
  color += u_ring * orbit * 0.35;
  float vignette = smoothstep(1.1, 0.2, dist);
  float alpha = (core * 0.85 + ring * 0.55 + orbit * 0.25) * vignette;
  gl_FragColor = vec4(color, alpha);
}
`;

function compileShader(gl: WebGLRenderingContext, type: number, source: string) {
  const shader = gl.createShader(type);
  if (!shader) return null;
  gl.shaderSource(shader, source);
  gl.compileShader(shader);
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
    gl.deleteShader(shader);
    return null;
  }
  return shader;
}

function createProgram(gl: WebGLRenderingContext) {
  const vs = compileShader(gl, gl.VERTEX_SHADER, VERT);
  const fs = compileShader(gl, gl.FRAGMENT_SHADER, FRAG);
  if (!vs || !fs) return null;
  const program = gl.createProgram();
  if (!program) return null;
  gl.attachShader(program, vs);
  gl.attachShader(program, fs);
  gl.linkProgram(program);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
    gl.deleteProgram(program);
    return null;
  }
  return program;
}

export function probeWebGL(): boolean {
  if (typeof document === "undefined") return false;
  try {
    const canvas = document.createElement("canvas");
    const gl = canvas.getContext("webgl", { alpha: true, antialias: true });
    return Boolean(gl);
  } catch {
    return false;
  }
}

export function createJarvisWebGL(canvas: HTMLCanvasElement) {
  const gl = canvas.getContext("webgl", { alpha: true, antialias: true, premultipliedAlpha: true });
  if (!gl) throw new Error("WebGL unavailable");

  const program = createProgram(gl);
  if (!program) throw new Error("WebGL program failed");

  const buffer = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);

  const aPosition = gl.getAttribLocation(program, "a_position");
  const uResolution = gl.getUniformLocation(program, "u_resolution");
  const uTime = gl.getUniformLocation(program, "u_time");
  const uInner = gl.getUniformLocation(program, "u_inner");
  const uOuter = gl.getUniformLocation(program, "u_outer");
  const uRing = gl.getUniformLocation(program, "u_ring");
  const uPulse = gl.getUniformLocation(program, "u_pulse");
  const uSpin = gl.getUniformLocation(program, "u_spin");

  let state: JarvisCoreVisualState = "HEALTHY_IDLE";
  let paused = false;
  let raf = 0;
  const start = performance.now();

  const resize = () => {
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const { clientWidth, clientHeight } = canvas;
    const w = Math.max(1, Math.floor(clientWidth * dpr));
    const h = Math.max(1, Math.floor(clientHeight * dpr));
    if (canvas.width !== w || canvas.height !== h) {
      canvas.width = w;
      canvas.height = h;
      gl.viewport(0, 0, w, h);
    }
  };

  const draw = (now: number) => {
    raf = requestAnimationFrame(draw);
    if (paused) return;
    resize();
    const palette = PALETTES[state];
    const t = (now - start) / 1000;
    gl.clearColor(0, 0, 0, 0);
    gl.clear(gl.COLOR_BUFFER_BIT);
    gl.enable(gl.BLEND);
    gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
    gl.useProgram(program);
    gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
    gl.enableVertexAttribArray(aPosition);
    gl.vertexAttribPointer(aPosition, 2, gl.FLOAT, false, 0, 0);
    gl.uniform2f(uResolution, canvas.width, canvas.height);
    gl.uniform1f(uTime, t);
    gl.uniform3fv(uInner, palette.inner);
    gl.uniform3fv(uOuter, palette.outer);
    gl.uniform3fv(uRing, palette.ring);
    gl.uniform1f(uPulse, palette.pulse);
    gl.uniform1f(uSpin, palette.spin);
    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
  };

  raf = requestAnimationFrame(draw);

  return {
    setState(next: JarvisCoreVisualState) {
      state = next;
    },
    setPaused(next: boolean) {
      paused = next;
    },
    resize,
    destroy() {
      cancelAnimationFrame(raf);
      gl.deleteProgram(program);
      gl.deleteBuffer(buffer);
    },
  };
}
