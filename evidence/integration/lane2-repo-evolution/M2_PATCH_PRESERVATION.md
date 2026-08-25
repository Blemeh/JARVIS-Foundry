# M2 PATCH PRESERVATION RECORD (evidence-only; owner directive 2026-08-24)

STATUS: PRESERVED
The accepted Wan2GP repair existed only on a local branch inside the disposable
lane clone (C:\temp\JARVIS-L2\wan2gp-baseline). Upstream push was
BLOCKED_EXTERNAL (403 denied to Blemeh) and remains so: no fork was created and
no upstream push was retried. This record makes the patch durable in the JARVIS
candidate evidence tree.

## Preserved artifact

PATCH FILE: M2_WAN2GP_LONGCAT_BSA_FIX_6E35B37_C767A48.patch
FORMAT: git format-patch -1 c767a48043c1bb13b917a8683bd71dc72234a6f6
BYTES: 3694
SHA256: 6CDBA83D2D3B10B704D7E6C28E6C60985C421678546AC44C290611D978C6843C

## Identity chain

BASE SHA:            6e35b37e309ccebeed193ef53cdff66fb973b693
                     (upstream deepbeepmeep/Wan2GP main at mission start)
ACCEPTED COMMIT:     c767a48043c1bb13b917a8683bd71dc72234a6f6
                     ("fix: repair indentation errors that made LongCat BSA
                      flash-attention unparseable"; parent verified == BASE SHA)
TARGET FILE:         models/longcat/modules/block_sparse_attention/flash_attn_bsa_varlen_mask.py
CHANGE:              +6/-6, indentation-only across _attn_fwd_bsa_varlen and
                     _attn_fwd_bsa_varlen_align

## Verification evidence (from original mission, unchanged)

VERIFIER: fresh independent verifier verdict PASS
(AC1 ast.parse OK all 8 kernels; AC2 whole-tree compileall OK; AC3 diff
confined/whitespace-only with exact indent deltas; P1 pristine blob proven
broken at line 111; critic ACCEPT 0 blocking). Details:
M2_OUTCOME.md / session log.

## Preservation-time integrity re-check (this directive)

git apply --check against pristine detached worktree at BASE SHA: EXIT=0
(patch applies cleanly to recorded base).

## Reproduction

git clone https://github.com/deepbeepmeep/Wan2GP.git
git checkout 6e35b37e309ccebeed193ef53cdff66fb973b693
git am M2_WAN2GP_LONGCAT_BSA_FIX_6E35B37_C767A48.patch

Boundary note per owner directive: do NOT create a fork; do NOT retry the
upstream push. Any future upstream contribution requires a separate
owner-authorized decision.
