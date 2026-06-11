import type { TrainingConfig } from "$lib/types";

// Style-vs-character preset overrides. The reference recipe targets
// styles; the notes flag that character LoRAs benefit from a higher LR.
export const PRESETS: Record<string, Partial<TrainingConfig>> = {
  style: {
    learning_rate: 2e-5,
    gradient_accumulation_steps: 4,
    epochs: 40,
  },
  character: {
    learning_rate: 5e-5,
    gradient_accumulation_steps: 2,
    epochs: 60,
  },
};

// Orthogonal to style/character: the VRAM profile that turns a recipe-
// default run into one that fits on an 8 GB card. Measured stable usage
// on this stack (single 512 bucket + rank 16 + 26/28 blocks swapped +
// AdamW8bitKahan + unsloth checkpointing + the WSL2 blocking-transfer
// shim) was ~6.4 GB on an RTX 4090; "8 GB" is the marketed ceiling with
// a little headroom for the activation peak.
//
// The 1024 bucket is dropped (biggest single lever — recipe defaults
// need ≈14 GB at 1024px vs ≈10 GB at 512px). Rank is halved for a small
// additional saving. 26 of Anima's 28 transformer blocks are CPU-offloaded
// — the max diffusion-pipe allows (assert blocks_to_swap <= num_blocks-2)
// — keeping only 2 blocks GPU-resident at steady-state. AdamW8bitKahan
// compresses the optimizer state by ~75%; unsloth checkpointing offloads
// saved hidden states to CPU between forward and backward.
// transformer_dtype stays bfloat16: fp8 is structurally broken for Anima
// (the LLM adapter's embedding has ndim==2 so diffusion-pipe quantizes
// it, and the downstream RMSNorm crashes on `fp8 * fp32_rsqrt`
// promotion). The fp8 line in the schema is preserved for forward-compat
// / forked diffusion-pipe builds, but the preset explicitly resets it to
// bfloat16 to clean up any stale fp8 value left by an earlier version
// of this preset.
// ``micro_batch_size`` and ``gradient_accumulation_steps`` are
// intentionally NOT in this profile. micro_batch_size is already 1 by
// recipe default (and 1 is the minimum); grad_accum doesn't affect VRAM
// at all — only effective batch size, which is a training-quality
// choice that belongs to the style/character preset. A previous version
// of this profile set grad_accum=4 and forgot to mirror it in
// LOW_VRAM_RESET, so toggling the chip OFF left users stuck at the
// wrong effective batch (character expects 2, style expects 4) and
// visibly under-trained character LoRAs.
export const LOW_VRAM_PROFILE: Partial<TrainingConfig> = {
  resolutions: [512],
  rank: 16,
  transformer_dtype: "bfloat16",
  // Max allowed by diffusion-pipe (`num_blocks - 2 = 26` for Anima's
  // 28-block DiT). Going to the ceiling — at 24 we still OOM during
  // setup because cosmos_predict2.py:432 unconditionally moves the
  // transformer's non-block parts (LLM adapter ~550 MB + embedders +
  // final layer) onto GPU before the swap loop runs.
  blocks_to_swap: 26,
  // 8-bit optimizer state — saves ~75% of optimizer-state VRAM.
  // bitsandbytes is already in diffusion-pipe's venv. Used by the
  // canonical wan_14b_min_vram example.
  optimizer_type: "AdamW8bitKahan",
  // Aggressive activation checkpointing — also from the wan example.
  activation_checkpointing_mode: "unsloth",
};
export const LOW_VRAM_RESET: Partial<TrainingConfig> = {
  resolutions: [512, 1024],
  rank: 32,
  transformer_dtype: "bfloat16",
  blocks_to_swap: 0,
  optimizer_type: "adamw_optimi",
  activation_checkpointing_mode: "default",
};

export type PathField = "diffusion_pipe_dir" | "dit_path" | "vae_path" | "llm_path";
export const PATH_FIELDS: {
  key: PathField;
  label: string;
  expect: "dir" | "file";
  placeholder: string;
  hint: string;
  tooltip: string;
}[] = [
  {
    key: "diffusion_pipe_dir",
    label: "diffusion-pipe directory",
    expect: "dir",
    placeholder: "/home/you/code/diffusion-pipe",
    hint: "Folder containing train.py (tdrussell/diffusion-pipe or compatible fork).",
    tooltip:
      "Local checkout of tdrussell/diffusion-pipe (or a compatible fork). Must contain train.py — that's the script we invoke via deepspeed.",
  },
  {
    key: "dit_path",
    label: "Anima DiT (transformer) file",
    expect: "file",
    placeholder: "/data/models/anima-base-v1.0.safetensors",
    hint: "anima-base-v1.0.safetensors (or a successor checkpoint).",
    tooltip:
      "Anima base diffusion transformer (the DiT) you're fine-tuning. Typically anima-base-v1.0.safetensors. This is the largest of the four files.",
  },
  {
    key: "vae_path",
    label: "Qwen image VAE",
    expect: "file",
    placeholder: "/data/models/qwen_image_vae.safetensors",
    hint: "qwen_image_vae.safetensors.",
    tooltip:
      "VAE used to encode images into latents during training. The Qwen image VAE that ships alongside Anima — usually qwen_image_vae.safetensors.",
  },
  {
    key: "llm_path",
    label: "Qwen 3 0.6B base text encoder",
    expect: "file",
    placeholder: "/data/models/qwen_3_06b_base.safetensors",
    hint: "qwen_3_06b_base.safetensors.",
    tooltip:
      "Text encoder Anima conditions on. Use the Qwen 3 0.6B base weights (qwen_3_06b_base.safetensors), not an instruct variant.",
  },
];

export const RESOLUTION_PRESETS: { key: string; label: string; values: number[] }[] = [
  { key: "512", label: "512", values: [512] },
  { key: "512+1024", label: "512 + 1024 (recommended)", values: [512, 1024] },
  { key: "512+1024+1536", label: "512 + 1024 + 1536 (max detail)", values: [512, 1024, 1536] },
  { key: "1024", label: "1024 only", values: [1024] },
];
