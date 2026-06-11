// One shared bulk-retag/describe runner. ActionBar's Tag/Describe buttons and
// App's t/s shortcuts both call this, replacing the two copy-pasted loops that
// lived in each (the Phase-3 duplication). It processes one filename at a time
// so each frame gets per-frame badge feedback and each LLM call uses a fresh
// chat context; failed frames stay selected as a retry hint. Per-item skip
// reasons (Phase-2 `skipped: [{filename, reason}]`) are surfaced in the toast.
//
// The runner is store-agnostic: callers pass the store hooks as `actions` so
// this stays a leaf module (no import cycle with framesStore/toasts).

import * as api from "$lib/api";

export type BulkRetagKind = "tag" | "describe";

/** Store/UI hooks the runner needs. Supplied by the caller so this module
 *  doesn't import the stores directly. */
export interface BulkRetagActions {
	/** Mark all filenames in-flight up front (queued spinners). */
	markProcessing: (filenames: string[]) => void;
	/** Clear one filename's in-flight state (per-frame, in the finally). */
	unmarkProcessing: (filename: string) => void;
	/** A frame succeeded — bust its sidecar cache. For "tag" pass the original
	 *  filename; for "describe" pass the effective (possibly `_crop`) filename. */
	markDone: (effectiveFilename: string) => void;
	/** A frame succeeded — drop it from the selection (drains the pill). */
	deselect: (filenames: string[]) => void;
	/** Surface a partial-failure summary (errors do not auto-dismiss). */
	error: (msg: string) => void;
}

/** Result summary, mostly for callers that want it; ActionBar/App ignore it. */
export interface BulkRetagResult {
	succeeded: number;
	failed: number;
	total: number;
}

/** Run the chosen bulk operation across `filenames`, one at a time.
 *  - `kind === "tag"`   → api.bulkRetagDanbooru, success when `retagged > 0`.
 *  - `kind === "describe"` → api.bulkRetagLLM,  success when `described > 0`.
 *  On any per-item failure (thrown error OR a `skipped` entry from the server)
 *  the frame is left selected and its reason is collected for the toast. */
export async function runBulkRetag(
	kind: BulkRetagKind,
	slug: string,
	filenames: string[],
	actions: BulkRetagActions,
): Promise<BulkRetagResult> {
	// Up-front so tiles not yet reached still show a queued spinner.
	actions.markProcessing(filenames);
	let succeeded = 0;
	// filename → reason, for frames that failed (thrown or server-skipped).
	const failures: { filename: string; reason: string }[] = [];

	for (const filename of filenames) {
		try {
			if (kind === "tag") {
				const res = await api.bulkRetagDanbooru(slug, [filename]);
				if (res.retagged > 0) {
					// Tags write back to the ORIGINAL sidecar even when a crop was
					// tagged, so bust the cache on the filename the user clicked.
					actions.markDone(filename);
					actions.deselect([filename]);
					succeeded += 1;
				} else {
					failures.push({
						filename,
						reason: res.skipped[0]?.reason ?? "unknown error",
					});
				}
			} else {
				const res = await api.bulkRetagLLM(slug, [filename]);
				if (res.described > 0) {
					// The backend may retarget to a `_crop` derivative; pop the badge
					// on the row that actually got written.
					const eff = res.effective_filenames?.[0] ?? filename;
					actions.markDone(eff);
					actions.deselect([filename]);
					succeeded += 1;
				} else {
					failures.push({
						filename,
						reason: res.skipped[0]?.reason ?? res.error ?? "unknown error",
					});
				}
			}
		} catch (e) {
			failures.push({
				filename,
				reason: e instanceof Error ? e.message : String(e),
			});
		} finally {
			actions.unmarkProcessing(filename);
		}
	}

	const failed = failures.length;
	if (failed > 0) {
		const verb = kind === "tag" ? "tag" : "describe";
		// Show up to 3 distinct reasons so the toast stays readable.
		const reasons = [...new Set(failures.map((f) => f.reason))].slice(0, 3);
		const reasonText = reasons.length ? ` (${reasons.join("; ")})` : "";
		actions.error(
			`${failed} of ${filenames.length} frame${filenames.length === 1 ? "" : "s"} ` +
				`failed to ${verb}${reasonText} — they stay selected so you can retry.`,
		);
	}

	return { succeeded, failed, total: filenames.length };
}
