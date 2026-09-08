import { useState, useRef } from "react";
import { Shield, ShieldAlert, ShieldCheck, ShieldX, ChevronDown, ChevronUp, AlertTriangle, AlertCircle, CheckCircle2, XCircle, DollarSign, Info, Printer } from "lucide-react";
import { printElement } from "../../utils/printReport";

const SEV_CONFIG = {
  CRITICAL: { color: "#7f1d1d", bg: "#fef2f2", border: "#fca5a5", badge: "#dc2626", label: "CRITICAL" },
  HIGH:     { color: "#78350f", bg: "#fff7ed", border: "#fdba74", badge: "#ea580c", label: "HIGH" },
  MEDIUM:   { color: "#713f12", bg: "#fefce8", border: "#fde047", badge: "#ca8a04", label: "MEDIUM" },
  LOW:      { color: "#1e3a5f", bg: "#eff6ff", border: "#93c5fd", badge: "#2563eb", label: "LOW" },
  UNKNOWN:  { color: "#374151", bg: "#f9fafb", border: "#d1d5db", badge: "#6b7280", label: "INFO" },
};

const BLOCKING_SEVERITIES = new Set(["CRITICAL", "HIGH"]);

function SeverityBadge({ severity }) {
  const cfg = SEV_CONFIG[severity?.toUpperCase()] || SEV_CONFIG.UNKNOWN;
  return (
    <span style={{
      display: "inline-block",
      padding: "1px 7px", borderRadius: 9, fontSize: 10, fontWeight: 700,
      background: cfg.badge + "22", color: cfg.badge,
      border: `1px solid ${cfg.badge}44`,
      letterSpacing: "0.04em",
    }}>
      {cfg.label}
    </span>
  );
}

function CheckRow({ check }) {
  const sev = (check.severity || "UNKNOWN").toUpperCase();
  const cfg = SEV_CONFIG[sev] || SEV_CONFIG.UNKNOWN;
  return (
    <div style={{
      padding: "8px 12px",
      borderLeft: `3px solid ${cfg.badge}`,
      background: cfg.bg,
      borderRadius: "0 6px 6px 0",
      fontSize: 12,
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        <code style={{ fontWeight: 700, color: cfg.badge, fontSize: 11 }}>{check.check_id}</code>
        <SeverityBadge severity={sev} />
        <span style={{ color: "#374151", fontWeight: 600 }}>{check.check_name || check.name || ""}</span>
        <span style={{ marginLeft: "auto", color: "#6b7280", fontSize: 11 }}>
          {check.resource || check.file || ""}
        </span>
      </div>
      {check.guideline && (
        <div style={{ marginTop: 4, color: "#6b7280", fontSize: 11 }}>
          {check.guideline}
        </div>
      )}
    </div>
  );
}

function CollapsibleSection({ title, icon, count, color, children, defaultOpen = false }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div style={{ marginBottom: 10 }}>
      <button
        onClick={() => setOpen(v => !v)}
        style={{
          width: "100%", display: "flex", alignItems: "center", gap: 8,
          padding: "8px 12px", borderRadius: 7, cursor: "pointer",
          background: "var(--surface-2,#f4f6fa)",
          border: "1px solid var(--surface-4,#e4e8f0)",
          fontSize: 12, fontWeight: 700, color,
        }}
      >
        {icon}
        {title}
        <span style={{
          padding: "1px 8px", borderRadius: 10, fontSize: 11,
          background: color + "22", color,
        }}>{count}</span>
        <span style={{ marginLeft: "auto" }}>
          {open ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
        </span>
      </button>
      {open && (
        <div style={{ display: "flex", flexDirection: "column", gap: 5, marginTop: 6 }}>
          {children}
        </div>
      )}
    </div>
  );
}

export default function SecurityPanel({ artifacts }) {
  const panelRef = useRef(null);
  const iacValidation = artifacts?.iac_validation || {};
  const securityScan  = iacValidation.security_scan || {};
  const tfValidation  = iacValidation.terraform_validation || {};
  const infracost     = iacValidation.estimated_monthly_cost || {};
  const sevClassification = iacValidation.security_failure_classification
    || artifacts?.checkov_failure_classification
    || null;

  const allFailedChecks = securityScan.failed_checks || [];
  const blockingChecks  = allFailedChecks.filter(c => BLOCKING_SEVERITIES.has((c.severity || "UNKNOWN").toUpperCase()));
  const warningChecks   = allFailedChecks.filter(c => !BLOCKING_SEVERITIES.has((c.severity || "UNKNOWN").toUpperCase()));

  const passed        = securityScan.passed ?? 0;
  const totalFailed   = allFailedChecks.length || securityScan.failed || 0;
  const blockingCount = blockingChecks.length;
  const warningCount  = warningChecks.length;
  const skipped       = securityScan.skipped ?? false;

  const tfValid       = tfValidation.valid ?? null;
  const tfDiagnostics = tfValidation.diagnostics || [];
  const tfInitError   = tfValidation.error || "";
  const planErrors    = typeof tfValidation.plan_errors === "string"
    ? tfValidation.plan_errors.trim()
    : "";
  const hasMeaningfulPlanError = planErrors.length > 15;

  // Detect if the terraform validate failure is only an auth/credential error
  // (AADSTS, tenant not found, placeholder UUID) — not a real HCL error.
  // These are non-blocking: the static validator uses dummy credentials.
  const _authErrorSignals = ["aadsts", "could not acquire access token", "clientcredentialstoken",
    "tenant '", "not found. check with your subscription", "parse claims", "building azurerm",
    "no valid credential", "arm_client_id", "arm_subscription_id"];
  const _planErrLow = planErrors.toLowerCase();
  const _initErrLow = tfInitError.toLowerCase();
  const tfValidFailIsAuthOnly = tfValid === false && tfDiagnostics.length === 0 &&
    _authErrorSignals.some(s => _planErrLow.includes(s) || _initErrLow.includes(s));

  const hasScan   = passed > 0 || totalFailed > 0;
  // Auth-only validate failures are non-blocking (placeholder credential limitation)
  const overallOk = (tfValid !== false || tfValidFailIsAuthOnly) && blockingCount === 0;

  if (!hasScan && tfValid === null && !infracost.monthly_cost) {
    return (
      <div style={{ padding: "24px", textAlign: "center", color: "var(--text-tertiary)", fontSize: 13 }}>
        <Shield size={28} style={{ marginBottom: 8, opacity: 0.4 }} />
        <div>Aucun résultat de scan de sécurité disponible pour cette migration.</div>
        <div style={{ fontSize: 12, marginTop: 4, opacity: 0.7 }}>
          Le scan Checkov s'exécute automatiquement après la génération du code IaC.
        </div>
      </div>
    );
  }

  const monthCost = infracost?.monthly_cost;
  const costNum   = typeof monthCost === "number" ? monthCost : parseFloat(monthCost);

  return (
    <div ref={panelRef} style={{ display: "flex", flexDirection: "column", gap: 14 }}>

      {/* ── PDF download button ── */}
      <div style={{ display: "flex", justifyContent: "flex-end" }}>
        <button
          onClick={() => printElement(panelRef.current, "Rapport Sécurité IaC — Checkov")}
          style={{
            display: "flex", alignItems: "center", gap: 5,
            padding: "5px 12px", borderRadius: 7, cursor: "pointer",
            background: "var(--surface-2,#f4f6fa)",
            border: "1px solid var(--surface-4,#e4e8f0)",
            fontSize: 12, fontWeight: 600, color: "var(--text-secondary)",
          }}
        >
          <Printer size={12} /> Télécharger PDF
        </button>
      </div>

      {/* ── Overall status banner ── */}
      <div style={{
        display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap",
        padding: "12px 16px",
        background: overallOk ? "#f0fdf4" : "#fef2f2",
        border: `1px solid ${overallOk ? "#86efac" : "#fca5a5"}`,
        borderRadius: 9,
      }}>
        {overallOk
          ? <ShieldCheck size={22} color="#16a34a" />
          : <ShieldAlert size={22} color="#dc2626" />}
        <div>
          <div style={{ fontWeight: 700, fontSize: 14, color: overallOk ? "#15803d" : "#991b1b" }}>
            {overallOk ? "Sécurité IaC : validée" : "Sécurité IaC : problèmes détectés"}
          </div>
          <div style={{ fontSize: 12, color: overallOk ? "#166534" : "#7f1d1d", marginTop: 2 }}>
            {blockingCount > 0 && `${blockingCount} échec(s) bloquant(s) (CRITICAL/HIGH)`}
            {blockingCount > 0 && warningCount > 0 && " · "}
            {warningCount > 0 && `${warningCount} avertissement(s) (MEDIUM/LOW)`}
            {blockingCount === 0 && warningCount === 0 && hasScan && "Aucune faille de sécurité détectée"}
          </div>
        </div>
        {/* Summary pills */}
        <div style={{ marginLeft: "auto", display: "flex", gap: 6, flexWrap: "wrap" }}>
          <span style={{ padding: "3px 10px", borderRadius: 10, fontSize: 11, fontWeight: 700, background: "#dcfce7", color: "#15803d" }}>
            ✓ {passed} passé(s)
          </span>
          {blockingCount > 0 && (
            <span style={{ padding: "3px 10px", borderRadius: 10, fontSize: 11, fontWeight: 700, background: "#fef2f2", color: "#dc2626" }}>
              ✗ {blockingCount} bloquant(s)
            </span>
          )}
          {warningCount > 0 && (
            <span style={{ padding: "3px 10px", borderRadius: 10, fontSize: 11, fontWeight: 700, background: "#fff7ed", color: "#ea580c" }}>
              ⚠ {warningCount} avertissement(s)
            </span>
          )}
        </div>
      </div>

      {/* ── Terraform validate status ── */}
      {tfValid !== null && (
        <div style={{
          padding: "10px 14px",
          background: tfValid ? "#f0fdf4" : tfValidFailIsAuthOnly ? "#fffbeb" : "#fef2f2",
          border: `1px solid ${tfValid ? "#86efac" : tfValidFailIsAuthOnly ? "#fde68a" : "#fca5a5"}`,
          borderRadius: 8, fontSize: 12,
        }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            {tfValid
              ? <CheckCircle2 size={16} color="#16a34a" />
              : tfValidFailIsAuthOnly
                ? <AlertCircle size={16} color="#d97706" />
                : <XCircle size={16} color="#dc2626" />}
            <span style={{ fontWeight: 600, color: tfValid ? "#15803d" : tfValidFailIsAuthOnly ? "#92400e" : "#991b1b" }}>
              Terraform validate : {tfValid ? "PASS" : tfValidFailIsAuthOnly ? "PASS (validation statique — credentials placeholder)" : "FAIL"}
            </span>
          </div>
          {tfValidFailIsAuthOnly && (
            <div style={{ marginTop: 6, fontSize: 11, color: "#92400e", fontStyle: "italic" }}>
              ℹ️ La validation statique utilise des credentials placeholder (pas de vraie connexion Azure). Ce résultat est non-bloquant — l'IaC HCL est syntaxiquement valide.
            </div>
          )}

          {/* Diagnostics from terraform validate -json */}
          {!tfValid && tfDiagnostics.length > 0 && (
            <div style={{ marginTop: 8, display: "flex", flexDirection: "column", gap: 4 }}>
              {tfDiagnostics.slice(0, 5).map((d, i) => (
                <div key={i} style={{
                  padding: "6px 10px", borderRadius: 5,
                  background: "#1e1e1e", color: "#f87171",
                  fontSize: 11, fontFamily: "monospace", whiteSpace: "pre-wrap",
                }}>
                  <span style={{ color: "#fbbf24", fontWeight: 700 }}>
                    {(d.severity || "error").toUpperCase()}
                  </span>
                  {" — "}{d.summary || ""}
                  {d.detail ? `\n${d.detail}` : ""}
                  {(d.range || {}).filename
                    ? `\n@ ${(d.range.filename || "").split("/").pop()}:${(d.range.start || {}).line || ""}`
                    : ""}
                </div>
              ))}
              {tfDiagnostics.length > 5 && (
                <div style={{ fontSize: 11, color: "#6b7280" }}>
                  … et {tfDiagnostics.length - 5} autre(s) diagnostic(s)
                </div>
              )}
            </div>
          )}

          {/* Plan-level errors (schema errors revealed by dry-run) */}
          {!tfValid && hasMeaningfulPlanError && tfDiagnostics.length === 0 && (
            <pre style={{
              marginTop: 8, padding: "8px 10px", borderRadius: 6,
              background: "#1e1e1e", color: "#f87171",
              fontSize: 11, fontFamily: "monospace", whiteSpace: "pre-wrap",
              maxHeight: 160, overflowY: "auto",
            }}>
              {planErrors.slice(0, 1200)}
            </pre>
          )}

          {/* Init/tool error (e.g. network, terraform not found) */}
          {!tfValid && tfInitError && !hasMeaningfulPlanError && tfDiagnostics.length === 0 && (
            <div style={{
              marginTop: 8, padding: "6px 10px", borderRadius: 5,
              background: "#1e1e1e", color: "#f87171",
              fontSize: 11, fontFamily: "monospace", whiteSpace: "pre-wrap",
            }}>
              {tfInitError.slice(0, 400)}
            </div>
          )}
        </div>
      )}

      {/* ── Category breakdown (SPE classification) ── */}
      {sevClassification && Object.values(sevClassification).some(v => v > 0) && (
        <div style={{
          padding: "12px 14px",
          background: "var(--surface-1,#fafafa)",
          border: "1px solid var(--surface-3,#eef1f7)",
          borderRadius: 8,
        }}>
          <div style={{ fontWeight: 700, fontSize: 12, color: "var(--text-secondary)", marginBottom: 8 }}>
            Catégories de sécurité détectées
          </div>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            {Object.entries(sevClassification).map(([cat, count]) => count > 0 && (
              <span key={cat} style={{
                padding: "3px 10px", borderRadius: 10, fontSize: 11, fontWeight: 700,
                background: "#fef2f2", color: "#991b1b",
                border: "1px solid #fca5a5",
              }}>
                {cat}: {count}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* ── Blocking failures ── */}
      {blockingCount > 0 && (
        <CollapsibleSection
          title="Échecs bloquants (CRITICAL / HIGH)"
          icon={<ShieldX size={14} />}
          count={blockingCount}
          color="#dc2626"
          defaultOpen
        >
          {blockingChecks.map((check, i) => (
            <CheckRow key={i} check={check} />
          ))}
        </CollapsibleSection>
      )}

      {/* ── Warnings ── */}
      {warningCount > 0 && (
        <CollapsibleSection
          title="Avertissements (MEDIUM / LOW — non bloquants)"
          icon={<AlertTriangle size={14} />}
          count={warningCount}
          color="#ca8a04"
          defaultOpen={blockingCount === 0}
        >
          {warningChecks.map((check, i) => (
            <CheckRow key={i} check={check} />
          ))}
        </CollapsibleSection>
      )}

      {/* ── Skipped notice ── */}
      {skipped && (
        <div style={{
          display: "flex", alignItems: "center", gap: 8,
          padding: "9px 13px",
          background: "#f0f9ff", border: "1px solid #bae6fd",
          borderRadius: 7, fontSize: 12, color: "#0369a1",
        }}>
          <Info size={14} />
          Certains checks ont été ignorés (dépendants de l'infrastructure en place : VNet, Key Vault, etc.).
        </div>
      )}

      {/* ── Infracost estimate ── */}
      {monthCost && monthCost !== "N/A" && (
        <div style={{
          display: "flex", alignItems: "center", gap: 10,
          padding: "11px 14px",
          background: "#f0fdf4", border: "1px solid #86efac",
          borderRadius: 8, fontSize: 13,
        }}>
          <DollarSign size={16} color="#16a34a" />
          <span style={{ fontWeight: 700, color: "#15803d" }}>Coût estimé mensuel :</span>
          <span style={{ color: "#166534" }}>
            {isNaN(costNum)
              ? String(monthCost)
              : `${costNum.toLocaleString("fr-FR", { maximumFractionDigits: 2 })} USD`}
          </span>
          {infracost.currency && infracost.currency !== "USD" && (
            <span style={{ fontSize: 11, color: "#6b7280" }}>({infracost.currency})</span>
          )}
        </div>
      )}

      {/* ── All-clear ── */}
      {hasScan && blockingCount === 0 && warningCount === 0 && (
        <div style={{
          display: "flex", alignItems: "center", gap: 8,
          padding: "10px 14px",
          background: "#f0fdf4", border: "1px solid #86efac",
          borderRadius: 7, fontSize: 12, color: "#15803d",
          fontWeight: 600,
        }}>
          <ShieldCheck size={16} />
          {passed > 0
            ? `Aucune faille détectée — ${passed} check(s) Checkov réussi(s).`
            : "Aucune faille de sécurité détectée par Checkov."}
        </div>
      )}
    </div>
  );
}
