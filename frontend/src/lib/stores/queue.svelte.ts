import * as api from "$lib/api";
import type { QueueItem, ServerEvent } from "$lib/types";

class QueueStore {
  items = $state<QueueItem[]>([]);
  status = $state<"open" | "closed" | "reconnecting">("closed");

  async refresh() {
    this.items = await api.listQueue();
  }

  ingest(event: ServerEvent) {
    if (event.type === "queue.update") {
      const queue = event.payload.queue as QueueItem[] | undefined;
      if (queue) this.items = queue;
    }
  }

  setStatus(s: "open" | "closed" | "reconnecting") {
    this.status = s;
  }

  running(): QueueItem | null {
    return this.items.find(i => i.status === "running") ?? null;
  }

  /** The failure reason for a job, or null if it isn't a failed job.
   *  This is the authoritative failure signal — the queue marks *every*
   *  uncaught exception as failed with an error string, including ones
   *  that crash before the pipeline emits any stage event. Returns a
   *  generic fallback if the job failed without an error string. */
  errorFor(jobId: string): string | null {
    const item = this.items.find(i => i.job_id === jobId);
    if (!item || item.status !== "failed") return null;
    return item.error ?? "Job failed";
  }

  pendingCount(): number {
    return this.items.filter(i => i.status === "pending").length;
  }

  totalActive(): number {
    return this.items.filter(i => i.status === "pending" || i.status === "running").length;
  }
}

export const queueStore = new QueueStore();
