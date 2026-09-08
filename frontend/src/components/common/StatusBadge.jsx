import { Loader2 } from "lucide-react";

const STATUS_MAP = {
  Created:            { cls: "badge-gray",    label: "Created",              spin: false },
  Analyzing:          { cls: "badge-cyan",    label: "Analyzing",            spin: true  },
  Analysis_Failed:    { cls: "badge-red",     label: "Analysis Failed",      spin: false },
  Plan_Ready:         { cls: "badge-purple",  label: "Plan Ready",           spin: false },
  Reviewing:          { cls: "badge-yellow",  label: "In Review",            spin: false },
  Accepted:           { cls: "badge-purple",  label: "Accepted",             spin: false },
  Rejected:           { cls: "badge-orange",  label: "Rejected",             spin: false },
  Generating_IaC:     { cls: "badge-cyan",    label: "Generating IaC",       spin: true  },
  Validating_Intent:  { cls: "badge-cyan",    label: "Validating",           spin: true  },
  IaC_Ready:          { cls: "badge-purple",  label: "IaC Ready",            spin: false },
  Correcting:         { cls: "badge-yellow",  label: "Correcting",           spin: true  },
  Deploying:          { cls: "badge-blue",    label: "Deploying",            spin: true  },
  Health_Checking:    { cls: "badge-cyan",    label: "Health Check",         spin: true  },
  Completed:          { cls: "badge-emerald", label: "Completed",            spin: false },
  Exported:           { cls: "badge-green",   label: "Exported",             spin: false },
  Failed:             { cls: "badge-red",     label: "Failed",               spin: false },
};

const ACTIVE_STATUSES = new Set([
  "Analyzing", "Generating_IaC", "Validating_Intent",
  "Deploying", "Health_Checking", "Correcting",
]);

export default function StatusBadge({ status }) {
  const info = STATUS_MAP[status] || { cls: "badge-gray", label: status || "Unknown", spin: false };
  const isActive = ACTIVE_STATUSES.has(status);

  return (
    <span className={`status-badge ${info.cls}`} style={{ display: "inline-flex", alignItems: "center", gap: 5 }}>
      {info.spin && (
        <Loader2 size={10} className="spin" style={{ flexShrink: 0 }} />
      )}
      {isActive && !info.spin && (
        <span style={{
          width: 6, height: 6, borderRadius: "50%",
          background: "currentColor", flexShrink: 0,
          animation: "pulse-dot 1.5s ease-in-out infinite",
        }} />
      )}
      {info.label}
    </span>
  );
}
