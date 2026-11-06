<a href="https://crossgl.net"><img src="assets/crossgl.png" align="right" width="84" alt="CrossGL logo"></a>

Hiya, I'm Nripesh.

I write compilers, mostly for GPU code. The main one is [CrossTL](https://github.com/CrossGL/crosstl), which translates shaders and compute kernels between CUDA, HIP, Metal, HLSL, GLSL, SPIR-V and Slang, and out to WGSL. I started it in 2024 and set up a small company around it, [CrossGL](https://crossgl.net). I'm in Sheffield, I moved here from Dubai for my master's.

This is the saxpy kernel from [NVIDIA's intro to CUDA](https://developer.nvidia.com/blog/easy-introduction-cuda-c-and-c/):

```cuda
__global__
void saxpy(int n, float a, float *x, float *y)
{
  int i = blockIdx.x*blockDim.x + threadIdx.x;
  if (i < n) y[i] = a*x[i] + y[i];
}
```

and this is the Metal that `crosstl translate saxpy.cu --backend metal` writes for it:

```metal
#include <metal_stdlib>
using namespace metal;

// Constant Buffers
struct saxpy_Args {
  int n;
  float a;
};
// Compute Shader
kernel void kernel_saxpy(uint3 thread_position_in_grid
                         [[thread_position_in_grid]],
                         constant saxpy_Args &saxpy_Args [[buffer(4)]],
                         device float *x [[buffer(2)]],
                         device float *y [[buffer(3)]]) {
  int i = thread_position_in_grid.x;
  if (i < saxpy_Args.n) {
    y[i] = saxpy_Args.a * x[i] + y[i];
  }
}
```

Everything goes through CrossGL, a small IR in the middle, so adding a language means writing one frontend and one backend. If CrossTL can't translate something, it stops with an error that names the construct and the target. Every night CI runs thousands of generated tests through it and checks the output with each language's own compiler or validator.

<details>
<summary>CI coverage chart</summary>
<br>
<a href="https://github.com/CrossGL/crosstl/blob/coverage/COVERAGE.md"><img src="https://raw.githubusercontent.com/CrossGL/crosstl/coverage/coverage.svg" width="100%" alt="CrossTL coverage chart from CI. For each language and direction it shows the share of generated feature tests where CrossTL's output is accepted by that language's own compiler or validator."></a>
</details>

There's also a [C++ compiler](https://github.com/CrossGL/compiler) for CrossGL itself, which is still pretty early.

### Open source

In 2023 I went round ML libraries telling them Macs have GPUs. The Apple Silicon (`mps`) support ended up in [Transformers](https://github.com/huggingface/transformers/pull/24660), [PyTorch Lightning](https://github.com/Lightning-AI/pytorch-lightning/pull/18127), [DeepSpeed](https://github.com/deepspeedai/DeepSpeed/pull/3907), [PEFT](https://github.com/huggingface/peft/pull/1217), [PyTorch Geometric](https://github.com/pyg-team/pytorch_geometric/pull/7770), [Kornia](https://github.com/kornia/kornia/pull/2425), [Haystack](https://github.com/deepset-ai/haystack/pull/6151), [MMPreTrain](https://github.com/open-mmlab/mmpretrain/pull/1699), [Raster Vision](https://github.com/azavea/raster-vision/pull/1985) and, in August 2026, [Ray](https://github.com/ray-project/ray/pull/38464).

In [MLX](https://github.com/ml-explore/mlx) I added some activation functions, `Dropout3d`, `logical_and` and `logical_or`, `clip_grad_norm`, `cross` and the orthogonal initialiser, and I'm on their [acknowledgements](https://github.com/ml-explore/mlx/blob/main/ACKNOWLEDGMENTS.md) list. I also started an [experimental ROCm backend](https://github.com/ml-explore/mlx/pull/2300) for it, which is still an open PR. I wrote the first version without an AMD GPU, so I found out it ran from someone else's screenshot in the thread.

Before CrossGL I was at [Unify](https://unify.ai), first writing code for [Ivy](https://github.com/unifyai/ivy) (41 merged PRs) and then running their open-source contributor programme.

### Papers

- [CrossTL: Hub-Based Translation of GPU Programs and the Preservation of Source Rules](https://arxiv.org/abs/2508.21256), arXiv, revised October 2026
- CrossGL: A Hub-and-Spoke Compiler Infrastructure for Universal Shader Translation Across Heterogeneous GPU Architectures, FTC 2026, in press
- [Hybrid Chess Search: Combining Alpha-Beta and Monte Carlo Tree Search on Unified Memory Architectures](https://doi.org/10.1007/978-3-032-32726-0_39), IntelliSys 2026
- [Integrating External Tools with Large Language Models (LLMs) to Improve Accuracy](https://doi.org/10.1007/978-981-96-1758-6_34), ICITA 2024, with a longer version on [arXiv](https://arxiv.org/abs/2507.08034)

### Chess

[MetalFish](https://github.com/NripeshN/MetalFish) is my chess engine. It's basically Stockfish's search on the CPU and an Lc0-style transformer search on the GPU, looking at the same position at the same time. In practice it plays about as well as the Stockfish half does on its own. Its [Lichess bot](https://lichess.org/@/Nripesh-MetalFish) ran on my MacBook between May and July and drew 982 of its 1,207 games.

I play too, for Sheffield University in the Sheffield & District league, and I'm about 1700 FIDE. On Lichess my engine's bullet rating is about 1,450 points higher than mine😅

<table>
<tr>
<td width="50%" valign="top"><a href="https://lichess.org/UROoH81r"><img src="https://lichess1.org/export/fen.gif?fen=rnbq1bnr/pppp1kpp/5P2/3NN3/5p2/8/PPPPQ1PP/R1B1KB1R_b_KQ_-_2_1&amp;color=white&amp;lastMove=f3e5" width="100%" alt="Chess position after 9.Ne5+ in a Vienna Gambit: white knights on d5 and e5, a white pawn on f6, the black king in check on f7. Black resigned."></a></td>
<td width="50%" valign="top"><a href="https://lichess.org/JA1XvXkf"><img src="https://lichess1.org/export/fen.gif?fen=8/5k2/p7/5Pp1/PP2p1Pp/2P1b2P/5q2/5K2_w_-_-_4_1&amp;color=black&amp;lastMove=d2f2" width="100%" alt="Chess position from black's side: the black queen on f2, protected by the bishop on e3, checkmates the white king on f1."></a></td>
</tr>
<tr>
<td valign="top">Me (white) against a 1900, nine moves into a Vienna Gambit. They resigned here.</td>
<td valign="top">MetalFish (black) mating a 3059-rated bot.</td>
</tr>
</table>

nripesh14@gmail.com
