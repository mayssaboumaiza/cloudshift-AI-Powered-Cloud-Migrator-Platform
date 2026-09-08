/**
 * useSSE.js — Reusable hook for real-time migration pipeline events via SSE.
 *
 * Opens ONE EventSource per migrationId and keeps it alive until the pipeline
 * reaches a terminal status. Previously the hook closed and reopened on every
 * status change (status was a useCallback dep), causing a race condition where
 * events emitted during the transition were missed and the frontend stayed stuck.
 */

import { useEffect, useRef, useState } from "react";

export const ACTIVE_STATUSES = new Set([
  "Analyzing",
  "Generating_IaC",
  "Validating_Intent",
  "Deploying",
  "Health_Checking",
  "Correcting",
  // Transient states that still need the stream open
  "Accepted",
  "IaC_Ready",
]);

// Statuses where the pipeline is definitively finished — close the stream.
const TERMINAL_STATUSES = new Set([
  "Plan_Ready",
  "Reviewing",
  "Completed",
  "Exported",
  "Failed",
  "Analysis_Failed",
]);

export const PHASE_LABELS = {
  iac_parser:         "Analyse du dépôt",
  cooldown:           "Pause cadence",
  ask_user_services:  "⏸ En attente de sélection manuelle des services",
  agent_01:           "Planification migration",
  check_plan:         "Validation du plan",
  ask_human:          "Décision utilisateur",
  agent_02:           "Génération Terraform",
  validate_intent:    "Vérification des contraintes",
  validate_iac:       "Validation IaC",
  agent_02_fix:       "Correction IaC",
  agent_03:           "Préparation déploiement",
  health_check:       "Vérification santé",
  export_zip:         "Export ZIP",
  correct_plan:       "Correction du plan",
  publish_github:     "Publication GitHub",
};

/**
 * @param {string|null} migrationId
 * @param {string|null} status     — current migration status from API
 * @param {function}    onRefresh  — called when a phase transition happens
 */
export function useSSE(migrationId, status, onRefresh) {
  const [connected, setConnected] = useState(false);
  const [lastEvent, setLastEvent] = useState(null);
  const esRef = useRef(null);
  // Keep a ref to onRefresh so the stable EventSource handler always calls
  // the latest version without needing to reopen the stream.
  const onRefreshRef = useRef(onRefresh);
  useEffect(() => { onRefreshRef.current = onRefresh; }, [onRefresh]);

  // Open the stream once per migrationId, skip if already terminal at mount.
  useEffect(() => {
    if (!migrationId) return;
    if (TERMINAL_STATUSES.has(status) && !ACTIVE_STATUSES.has(status)) return;

    // Already open for this migration — don't reopen.
    if (esRef.current) return;

    const es = new EventSource(`/api/v1/migrations/${migrationId}/stream`);
    esRef.current = es;

    es.onopen = () => setConnected(true);

    es.onmessage = (evt) => {
      try {
        const event = JSON.parse(evt.data);
        if (event.phase && PHASE_LABELS[event.phase]) {
          event.phase_label = PHASE_LABELS[event.phase];
        }
        setLastEvent(event);

        if (
          event.event_type === "phase_completed"      ||
          event.event_type === "phase_started"        ||
          event.event_type === "phase_progress"       ||
          event.event_type === "checkpoint_saved"     ||
          event.event_type === "human_input_required" ||
          event.event_type === "service_selection_required" ||
          event.event_type === "error_occurred"
        ) {
          onRefreshRef.current?.();
        }
      } catch { /* ignore malformed frames */ }
    };

    es.addEventListener("error", (evt) => {
      if (evt.data) {
        try {
          const payload = JSON.parse(evt.data);
          setLastEvent({ event_type: "error_occurred", ...payload });
        } catch { /* ignore */ }
      }
    });

    es.onerror = () => setConnected(false);

    return () => {
      es.close();
      esRef.current = null;
      setConnected(false);
    };
  }, [migrationId]); // eslint-disable-line react-hooks/exhaustive-deps
  // Intentionally omitting `status` — closing/reopening on status change
  // caused the race condition where transition events were missed.

  // Close the stream when a terminal status is confirmed by the API.
  useEffect(() => {
    if (TERMINAL_STATUSES.has(status) && esRef.current) {
      esRef.current.close();
      esRef.current = null;
      setConnected(false);
    }
  }, [status]);

  return { connected, lastEvent };
}
