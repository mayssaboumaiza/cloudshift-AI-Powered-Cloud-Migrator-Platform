const PRINT_CSS = `
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    font-size: 13px; color: #1e293b; background: #fff;
    padding: 32px 40px; line-height: 1.65;
  }
  .print-header {
    display: flex; align-items: center; gap: 14px;
    padding-bottom: 16px; margin-bottom: 24px;
    border-bottom: 2px solid #e2e8f0;
  }
  .print-logo {
    width: 38px; height: 38px; border-radius: 10px;
    background: linear-gradient(135deg, #0ea5e9, #6366f1);
    display: flex; align-items: center; justify-content: center;
    color: #fff; font-weight: 800; font-size: 16px; flex-shrink: 0;
  }
  .print-title { font-size: 20px; font-weight: 800; color: #0f172a; }
  .print-subtitle { font-size: 11px; color: #94a3b8; margin-top: 2px; }
  .print-date { margin-left: auto; font-size: 11px; color: #94a3b8; text-align: right; }
  button, [data-no-print], .btn { display: none !important; }
  table { border-collapse: collapse; width: 100%; font-size: 12px; page-break-inside: auto; }
  th { background: #f8fafc; padding: 8px 12px; text-align: left; border-bottom: 2px solid #e2e8f0; font-weight: 700; font-size: 11px; text-transform: uppercase; letter-spacing: 0.04em; color: #64748b; }
  td { padding: 7px 12px; border-bottom: 1px solid #f1f5f9; }
  tr:last-child td { border-bottom: none; }
  [style*="border-radius"] { break-inside: avoid; }
  pre, code { font-family: "Courier New", monospace; }
  pre { background: #0f172a; color: #e2e8f0; padding: 12px 16px; border-radius: 8px; font-size: 11px; white-space: pre-wrap; word-break: break-all; }
  h1 { font-size: 20px; font-weight: 800; color: #0f172a; margin: 0 0 14px; padding-bottom: 8px; border-bottom: 2px solid #e2e8f0; }
  h2 { font-size: 15px; font-weight: 700; color: #1e293b; margin: 24px 0 8px; padding-left: 8px; border-left: 3px solid #6366f1; }
  h3 { font-size: 13px; font-weight: 700; color: #1e293b; margin: 14px 0 5px; }
  .section { margin-bottom: 28px; page-break-inside: avoid; }
  .badge { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 11px; font-weight: 700; }
  .badge-blue   { background: #dbeafe; color: #1d4ed8; }
  .badge-green  { background: #dcfce7; color: #15803d; }
  .badge-red    { background: #fef2f2; color: #dc2626; }
  .badge-orange { background: #fff7ed; color: #c2410c; }
  .badge-gray   { background: #f3f4f6; color: #374151; }
  .badge-purple { background: #e0e7ff; color: #4338ca; }
  .info-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin: 12px 0; }
  .info-box { padding: 10px 14px; background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; }
  .info-label { font-size: 10px; font-weight: 700; text-transform: uppercase; color: #94a3b8; margin-bottom: 3px; }
  .info-value { font-size: 13px; font-weight: 600; color: #0f172a; }
  .service-row { display: flex; align-items: center; gap: 10px; padding: 10px 14px; border: 1px solid #e2e8f0; border-radius: 8px; margin-bottom: 8px; }
  .arrow { color: #94a3b8; font-size: 14px; }
  .rejected-banner { background: #fef2f2; border: 1px solid #fca5a5; border-radius: 8px; padding: 10px 14px; margin-bottom: 16px; color: #991b1b; font-weight: 700; font-size: 13px; }
  span[style*="borderRadius"] { print-color-adjust: exact; -webkit-print-color-adjust: exact; }
  @page { margin: 18mm 15mm; }
  @media print { * { print-color-adjust: exact; -webkit-print-color-adjust: exact; } }
`;

function printWindow(title, bodyHtml) {
  const date = new Date().toLocaleDateString("fr-FR", { day: "2-digit", month: "long", year: "numeric" });
  const time = new Date().toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
  const html = `<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8" />
  <title>${title}</title>
  <style>${PRINT_CSS}</style>
</head>
<body>
  <div class="print-header">
    <div class="print-logo">C</div>
    <div>
      <div class="print-title">${title}</div>
      <div class="print-subtitle">Généré par CloudShift AI</div>
    </div>
    <div class="print-date">${date}<br/>${time}</div>
  </div>
  ${bodyHtml}
</body>
</html>`;
  const win = window.open("", "_blank", "width=960,height=750");
  if (!win) return;
  win.document.write(html);
  win.document.close();
  win.focus();
  setTimeout(() => { win.print(); win.close(); }, 450);
}

/* ── Strategy color helper ───────────────────────────────────────────────── */
const STRATEGY_BADGE = {
  rehost:     "badge-blue",
  replatform: "badge-purple",
  refactor:   "badge-purple",
  retire:     "badge-red",
  retain:     "badge-gray",
  repurchase: "badge-green",
  relocate:   "badge-orange",
};
function strategyBadgeHtml(strategy) {
  const cls = STRATEGY_BADGE[(strategy || "").toLowerCase()] || "badge-gray";
  return `<span class="badge ${cls}">${strategy || "—"}</span>`;
}

/**
 * Génère et télécharge un PDF complet lors d'un rejet total de migration.
 * Contient : métadonnées, plan de migration complet, rapport de prévisualisation.
 *
 * @param {object} bundle — { migration_id, repo_url, source_cloud, target_cloud,
 *                            exported_at, migration_plan, preview_report,
 *                            dependency_graph, rejection_reason? }
 */
export function printRejectionReport(bundle) {
  const {
    migration_id,
    repo_url,
    source_cloud,
    target_cloud,
    exported_at,
    migration_plan,
    preview_report,
    rejection_reason,
  } = bundle || {};

  const plan = migration_plan || {};
  const resources = plan.resources || [];
  const summary = plan.summary || {};

  /* ── Section 1 : Infos générales ── */
  const metaHtml = `
    <div class="section">
      <h2>Informations de la migration</h2>
      <div class="rejected-banner">
        ❌ Plan rejeté — pipeline arrêté à l'étape de validation humaine
        ${rejection_reason ? `<div style="margin-top:4px;font-weight:400;font-size:12px;">Raison : ${rejection_reason}</div>` : ""}
      </div>
      <div class="info-grid">
        <div class="info-box"><div class="info-label">ID Migration</div><div class="info-value" style="font-size:11px;word-break:break-all">${migration_id || "—"}</div></div>
        <div class="info-box"><div class="info-label">Exporté le</div><div class="info-value">${exported_at ? new Date(exported_at).toLocaleString("fr-FR") : "—"}</div></div>
        <div class="info-box"><div class="info-label">Cloud source</div><div class="info-value">${source_cloud || "—"}</div></div>
        <div class="info-box"><div class="info-label">Cloud cible</div><div class="info-value">${target_cloud || "—"}</div></div>
        ${repo_url ? `<div class="info-box" style="grid-column:span 2"><div class="info-label">Repository</div><div class="info-value" style="font-size:11px;word-break:break-all">${repo_url}</div></div>` : ""}
      </div>
    </div>`;

  /* ── Section 2 : Résumé du plan ── */
  const summaryHtml = summary && Object.keys(summary).length > 0 ? `
    <div class="section">
      <h2>Résumé du plan de migration</h2>
      <div class="info-grid">
        ${summary.total_services ? `<div class="info-box"><div class="info-label">Services analysés</div><div class="info-value">${summary.total_services}</div></div>` : ""}
        ${summary.estimated_cost_usd != null ? `<div class="info-box"><div class="info-label">Coût estimé / mois</div><div class="info-value">${Number(summary.estimated_cost_usd).toLocaleString("fr-FR")} USD</div></div>` : ""}
        ${summary.migration_complexity ? `<div class="info-box"><div class="info-label">Complexité</div><div class="info-value">${summary.migration_complexity}</div></div>` : ""}
        ${summary.estimated_duration_weeks ? `<div class="info-box"><div class="info-label">Durée estimée</div><div class="info-value">${summary.estimated_duration_weeks} semaines</div></div>` : ""}
      </div>
    </div>` : "";

  /* ── Section 3 : Services ── */
  const servicesHtml = resources.length > 0 ? `
    <div class="section">
      <h2>Services analysés (${resources.length})</h2>
      <table>
        <thead>
          <tr>
            <th>#</th>
            <th>Service source</th>
            <th>Équivalent cible</th>
            <th>Stratégie</th>
            <th>Priorité</th>
          </tr>
        </thead>
        <tbody>
          ${resources.map((r, i) => `
            <tr>
              <td style="color:#94a3b8;font-size:11px">${i + 1}</td>
              <td><strong>${r.source_service || r.service_name || r.resource_name || "—"}</strong>
                ${r.source_type ? `<div style="font-size:11px;color:#94a3b8">${r.source_type}</div>` : ""}
              </td>
              <td>${r.target_service || r.target_equivalent || "—"}
                ${r.target_provider ? `<div style="font-size:11px;color:#94a3b8">${r.target_provider}</div>` : ""}
              </td>
              <td>${strategyBadgeHtml(r.migration_strategy || r.strategy)}</td>
              <td>${r.priority != null ? `<span class="badge ${r.priority <= 2 ? "badge-red" : r.priority <= 4 ? "badge-orange" : "badge-green"}">${r.priority}</span>` : "—"}</td>
            </tr>`).join("")}
        </tbody>
      </table>
    </div>` : "";

  /* ── Section 4 : Rapport de prévisualisation (markdown → texte brut) ── */
  const reportHtml = preview_report ? `
    <div class="section" style="page-break-before:always">
      <h2>Rapport de prévisualisation</h2>
      <div style="margin-top:12px;line-height:1.8;font-size:13px;white-space:pre-wrap;color:#334155;background:#f8fafc;padding:16px 20px;border-radius:8px;border:1px solid #e2e8f0;font-family:inherit">
${escapeHtml(preview_report)}
      </div>
    </div>` : "";

  const body = metaHtml + summaryHtml + servicesHtml + reportHtml;
  printWindow(`Rapport d'export — Migration ${(migration_id || "").slice(0, 8).toUpperCase()}`, body);
}

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

/**
 * Ouvre une fenêtre d'impression contenant le contenu HTML d'un élément DOM,
 * avec les styles inline + un CSS print propre.
 *
 * @param {HTMLElement} el   — élément racine à imprimer
 * @param {string}      title — titre affiché dans l'en-tête PDF
 */
export function printElement(el, title = "Rapport CloudShift") {
  if (!el) return;
  const cloned = el.cloneNode(true);
  el.querySelectorAll("svg").forEach((svg, i) => {
    const clone = cloned.querySelectorAll("svg")[i];
    if (clone) clone.setAttribute("xmlns", "http://www.w3.org/2000/svg");
  });
  printWindow(title, cloned.outerHTML);
}
