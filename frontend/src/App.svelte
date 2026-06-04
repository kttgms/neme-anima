<script lang="ts">
  import { onMount, onDestroy } from "svelte";
  import { projectsStore } from "$lib/stores/projects.svelte";
  import { viewStore } from "$lib/stores/view.svelte";
  import { queueStore } from "$lib/stores/queue.svelte";
  import { jobsStore } from "$lib/stores/jobs.svelte";
  import { framesStore } from "$lib/stores/frames.svelte";
  import { trainingStore } from "$lib/stores/training.svelte";
  import { connectEvents, type Connection } from "$lib/ws";
  import TopStrip from "$lib/components/TopStrip.svelte";
  import FramesTab from "$lib/components/FramesTab.svelte";
  import RegexModal from "$lib/components/RegexModal.svelte";
  import CreateProjectModal from "$lib/components/CreateProjectModal.svelte";
  import DeleteProjectModal from "$lib/components/DeleteProjectModal.svelte";
  import ConfirmFrameOverwriteModal from "$lib/components/ConfirmFrameOverwriteModal.svelte";
  import SourcesTab from "$lib/components/SourcesTab.svelte";
  import SettingsTab from "$lib/components/SettingsTab.svelte";
  import TrainingTab from "$lib/components/TrainingTab.svelte";

  let conn: Connection | null = null;
  let regexOpen = $state(false);
  let createOpen = $state(false);
  // Modal lives at the App root, not inside ProjectPills — the top bar
  // uses backdrop-blur which creates a CSS containing block for
  // position:fixed descendants, clipping any modal mounted under it.
  let deleteProjectOpen = $state(false);
  type FrameOverwriteRequest = {
    action: "retag" | "describe";
    selectedCount: number;
    affectedCount: number;
    resolve: (confirmed: boolean) => void;
  };
  let frameOverwriteRequest = $state<FrameOverwriteRequest | null>(null);

  async function confirmDeleteActiveProject() {
    const slug = projectsStore.active?.slug;
    if (!slug) return;
    await projectsStore.delete(slug);
    deleteProjectOpen = false;
  }

  function confirmFrameOverwrite(
    action: "retag" | "describe",
    selectedCount: number,
    affectedCount: number,
  ): Promise<boolean> {
    frameOverwriteRequest?.resolve(false);
    return new Promise((resolve) => {
      frameOverwriteRequest = { action, selectedCount, affectedCount, resolve };
    });
  }

  function resolveFrameOverwrite(confirmed: boolean) {
    const req = frameOverwriteRequest;
    if (!req) return;
    frameOverwriteRequest = null;
    req.resolve(confirmed);
  }

  onMount(async () => {
    await projectsStore.refresh();
    if (projectsStore.list.length > 0) {
      await projectsStore.load(projectsStore.list[0].slug);
    }
    await queueStore.refresh();
    conn = connectEvents({
      url: `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/api/ws`,
      onEvent: (ev) => {
        queueStore.ingest(ev);
        jobsStore.ingest(ev);
        trainingStore.ingest(ev);
      },
      onStatus: (s) => queueStore.setStatus(s),
    });
    window.addEventListener("keydown", onKey);
  });

  onDestroy(() => {
    conn?.close();
    window.removeEventListener("keydown", onKey);
  });

  function onKey(ev: KeyboardEvent) {
    const target = ev.target as HTMLElement | null;
    if (target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable)) return;
    // Don't run global frames shortcuts (a / d / Escape) when a modal is open —
    // otherwise Escape would close the modal AND clear the selection.
    if (document.querySelector('[role="dialog"]')) return;
    if (viewStore.tab !== "frames") return;
    if (ev.key === "a" && !ev.ctrlKey && !ev.metaKey) {
      framesStore.selectAll();
      ev.preventDefault();
    } else if (ev.key === "d" || ev.key === "Escape") {
      framesStore.clear();
      ev.preventDefault();
    }
  }
</script>

<div class="min-h-screen bg-ink-950 text-slate-100">
  <TopStrip
    onopenRegex={() => (regexOpen = true)}
    onopenCreate={() => (createOpen = true)}
    onopenDelete={() => (deleteProjectOpen = true)}
    onconfirmFrameOverwrite={confirmFrameOverwrite}
  />
  <main class="px-4 pb-12">
    {#if projectsStore.active}
      {#if viewStore.tab === "sources"}
        <SourcesTab />
      {:else if viewStore.tab === "frames"}
        <FramesTab onconfirmFrameOverwrite={confirmFrameOverwrite} />
      {:else if viewStore.tab === "training"}
        <TrainingTab />
      {:else if viewStore.tab === "settings"}
        <SettingsTab />
      {/if}
    {:else}
      <div class="flex flex-col items-center justify-center py-32 text-slate-400">
        <p class="text-lg mb-2">No project selected.</p>
        <p class="text-sm">Click "+" in the top bar to create one.</p>
      </div>
    {/if}
  </main>

  {#if regexOpen}
    <RegexModal onclose={() => (regexOpen = false)} />
  {/if}
  {#if createOpen}
    <CreateProjectModal onclose={() => (createOpen = false)} />
  {/if}
  {#if deleteProjectOpen && projectsStore.active}
    <DeleteProjectModal
      project={projectsStore.active}
      onconfirm={confirmDeleteActiveProject}
      oncancel={() => (deleteProjectOpen = false)}
    />
  {/if}
  {#if frameOverwriteRequest}
    <ConfirmFrameOverwriteModal
      action={frameOverwriteRequest.action}
      selectedCount={frameOverwriteRequest.selectedCount}
      affectedCount={frameOverwriteRequest.affectedCount}
      onconfirm={() => resolveFrameOverwrite(true)}
      oncancel={() => resolveFrameOverwrite(false)}
    />
  {/if}
</div>
