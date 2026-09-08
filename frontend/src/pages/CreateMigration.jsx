import { useState, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { motion, AnimatePresence } from "framer-motion";
import {
  Rocket, ArrowLeft, GitBranch, Cloud, DollarSign,
  Shield, MapPin, CheckCircle2, XCircle, Loader, Key,
  ChevronRight, Search, Lock, Globe,
} from "lucide-react";
import {
  createMigration,
  updateMigration,
  fetchGithubRepos,
  storeCredentials,
} from "../api/migrationApi";
import Alert from "../components/common/Alert";
import { useI18n } from "../context/I18nContext";

/* ══════════════════════════════════════════════════════════════════════════
   CONSTANTS
   ══════════════════════════════════════════════════════════════════════════ */
const CLOUDS = [
  { value: "aws",   label: "Amazon Web Services", color: "#FF9900" },
  { value: "gcp",   label: "Google Cloud",         color: "#4285F4" },
  { value: "azure", label: "Microsoft Azure",       color: "#0078D4" },
];

const REGIONS_BY_CLOUD = {
  aws: [
    { value: "",               labelKey: "create.region.no.pref" },
    { value: "us-east-1",      label: "us-east-1 (N. Virginia)" },
    { value: "us-east-2",      label: "us-east-2 (Ohio)" },
    { value: "us-west-2",      label: "us-west-2 (Oregon)" },
    { value: "eu-west-1",      label: "eu-west-1 (Ireland)" },
    { value: "eu-west-3",      label: "eu-west-3 (Paris)" },
    { value: "eu-central-1",   label: "eu-central-1 (Frankfurt)" },
    { value: "ap-southeast-1", label: "ap-southeast-1 (Singapore)" },
    { value: "ap-northeast-1", label: "ap-northeast-1 (Tokyo)" },
    { value: "sa-east-1",      label: "sa-east-1 (São Paulo)" },
  ],
  gcp: [
    { value: "",                    labelKey: "create.region.no.pref" },
    { value: "us-central1",         label: "us-central1 (Iowa)" },
    { value: "us-east1",            label: "us-east1 (South Carolina)" },
    { value: "us-west1",            label: "us-west1 (Oregon)" },
    { value: "europe-west1",        label: "europe-west1 (Belgium)" },
    { value: "europe-west4",        label: "europe-west4 (Netherlands)" },
    { value: "europe-west9",        label: "europe-west9 (Paris)" },
    { value: "asia-southeast1",     label: "asia-southeast1 (Singapore)" },
    { value: "asia-east1",          label: "asia-east1 (Taiwan)" },
    { value: "southamerica-east1",  label: "southamerica-east1 (São Paulo)" },
  ],
  azure: [
    { value: "",                    labelKey: "create.region.no.pref" },
    { value: "germanywestcentral",  label: "germanywestcentral (Frankfurt) ✓ Student" },
    { value: "swedencentral",       label: "swedencentral (Gävle) ✓ Student" },
    { value: "switzerlandnorth",    label: "switzerlandnorth (Zurich) ✓ Student" },
    { value: "italynorth",          label: "italynorth (Milan) ✓ Student" },
    { value: "eastus",              label: "eastus (Virginia)" },
    { value: "eastus2",             label: "eastus2 (Virginia)" },
    { value: "westus2",             label: "westus2 (Washington)" },
    { value: "westeurope",          label: "westeurope (Netherlands)" },
    { value: "northeurope",         label: "northeurope (Ireland)" },
    { value: "francecentral",       label: "francecentral (Paris)" },
    { value: "austriaeast",         label: "austriaeast (Vienna)" },
    { value: "southeastasia",       label: "southeastasia (Singapore)" },
    { value: "japaneast",           label: "japaneast (Tokyo)" },
    { value: "brazilsouth",         label: "brazilsouth (São Paulo)" },
  ],
};

/* ══════════════════════════════════════════════════════════════════════════
   SUB-COMPONENTS
   ══════════════════════════════════════════════════════════════════════════ */

function ValidationBadge({ state }) {
  if (!state) return null;
  if (state === "loading")
    return <Loader size={16} style={{ animation: "spin 1s linear infinite", color: "var(--text-disabled)", flexShrink: 0 }} />;
  if (state.valid)
    return (
      <span className="validation-badge valid">
        <CheckCircle2 size={14} /> {state.login || state.account || "Valid"}
      </span>
    );
  return (
    <span className="validation-badge invalid">
      <XCircle size={14} /> {state.error || "Invalid"}
    </span>
  );
}

function CloudSelector({ label, value, onChange, otherValue, otherSideTitle }) {
  return (
    <div className="cloud-selector">
      <label className="form-label">{label}</label>
      <div className="cloud-selector-grid">
        {CLOUDS.map((c) => {
          const isSelected  = value === c.value;
          const isOtherSide = otherValue === c.value;
          return (
            <button
              key={c.value}
              type="button"
              className={`cloud-option${isSelected ? " selected" : ""}${isOtherSide ? " other-side" : ""}`}
              style={{ "--provider-color": c.color }}
              onClick={() => onChange(c.value)}
              title={isOtherSide ? otherSideTitle : c.label}
            >
              <img src={`/${c.value}.svg`} alt={c.label} width={28} height={28}
                onError={(e) => { e.target.style.display = "none"; }}
              />
              <span>{c.value.toUpperCase()}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

function WizardProgress({ current, steps }) {
  return (
    <div className="wizard-progress">
      {steps.map((step, i) => {
        const isDone    = i < current;
        const isActive  = i === current;
        const stateClass = isDone ? "completed" : isActive ? "active" : "";
        return (
          <div key={step.id} className={`wizard-step-item ${stateClass}`}>
            <div className="wizard-step-node">
              <div className="wizard-step-indicator">
                {isDone ? <CheckCircle2 size={14} /> : i + 1}
              </div>
              <span className="wizard-step-label">{step.label}</span>
            </div>
            {i < steps.length - 1 && (
              <div className="wizard-connector">
                <div className="wizard-connector-fill" style={{ width: isDone ? "100%" : "0%" }} />
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

const slideVariants = {
  enter: (dir) => ({ opacity: 0, x: dir > 0 ? 32 : -32 }),
  center: { opacity: 1, x: 0 },
  exit:  (dir) => ({ opacity: 0, x: dir > 0 ? -32 : 32 }),
};

/* ── Step 1 ── */
function StepSource({ form, setForm, tokenValidation, setTokenValidation, handleTokenBlur,
  availableRepos, loadingRepos, handleLoadRepos, selectedRepos, toggleRepoSelection, t }) {

  const set = (key) => (e) => setForm((f) => ({ ...f, [key]: e.target.value }));

  const formatRelativeDate = (isoDate) => {
    if (!isoDate) return "";
    const diff = Date.now() - new Date(isoDate);
    const m = Math.floor(diff / 60000);
    if (m < 1)  return t("dashboard.instant");
    if (m < 60) return t("activity.ago.min", { n: m });
    const h = Math.floor(m / 60);
    if (h < 24) return t("activity.ago.hour", { n: h });
    const d = Math.floor(h / 24);
    if (d < 7)  return t("activity.ago.day", { n: d });
    return `${Math.floor(d / 7)}w ago`;
  };

  return (
    <div>
      <h3 className="wizard-step-title">{t("create.step1.title")}</h3>
      <p className="wizard-step-desc">{t("create.step1.desc")}</p>

      <div className="form-grid">
        <div className="form-group col-full">
          <label className="form-label">{t("create.github.url.label")}</label>
          <input
            className="form-input"
            placeholder="https://github.com/owner-or-org"
            value={form.github_url}
            onChange={set("github_url")}
          />
        </div>

        <div className="form-group col-full">
          <label className="form-label">
            {t("create.github.token.label")} <span style={{ color: "var(--error-solid)" }}>*</span>
          </label>
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <input
              className="form-input"
              type="password"
              placeholder="YOUR_GITHUB_TOKEN"
              value={form.github_token}
              onChange={(e) => { set("github_token")(e); setTokenValidation(null); }}
              onBlur={handleTokenBlur}
              required
              style={{ flex: 1 }}
            />
            <ValidationBadge state={tokenValidation} />
          </div>
          {tokenValidation && tokenValidation !== "loading" && !tokenValidation.valid &&
            tokenValidation.missing_permissions?.length > 0 && (
            <div style={{ marginTop: 4, fontSize: 11, color: "var(--error-solid)" }}>
              {t("create.missing.perms", { perms: tokenValidation.missing_permissions.join(", ") })}
            </div>
          )}
          <span className="form-hint" dangerouslySetInnerHTML={{ __html: t("create.github.token.hint") }} />
        </div>

        <div className="form-group col-full">
          <label className="form-label">
            {t("create.repo.url.label")} <span style={{ color: "var(--error-solid)" }}>*</span>
          </label>
          <input
            className="form-input"
            placeholder="https://github.com/owner/repo"
            value={form.repo_url}
            onChange={set("repo_url")}
            required
          />
          <span className="form-hint">{t("create.repo.url.hint")}</span>
        </div>

        <div className="form-group col-full" style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
          <button
            type="button"
            className="btn btn-secondary"
            disabled={loadingRepos || !form.github_token || !form.github_url}
            onClick={handleLoadRepos}
          >
            <Search size={14} />
            {loadingRepos ? t("create.loading.repos") : t("create.load.repos")}
          </button>
          {selectedRepos.length > 0 && (
            <span style={{ fontSize: 12, color: "var(--brand-500)", display: "flex", alignItems: "center", gap: 4 }}>
              {t("create.repos.selected", { n: selectedRepos.length, s: selectedRepos.length !== 1 ? "s" : "" })}
            </span>
          )}
        </div>

        {availableRepos.length > 0 && (
          <div className="form-group col-full">
            <label className="form-label">
              {t("create.repos.available", { n: availableRepos.length })}
            </label>
            <div className="repo-picker">
              {availableRepos.map((repo) => {
                const isSelected = selectedRepos.includes(repo.full_name);
                return (
                  <div
                    key={repo.full_name}
                    className={`repo-item${isSelected ? " selected" : ""}`}
                    onClick={() => toggleRepoSelection(repo.full_name)}
                  >
                    <input
                      type="checkbox"
                      checked={isSelected}
                      onChange={() => toggleRepoSelection(repo.full_name)}
                      onClick={(e) => e.stopPropagation()}
                    />
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                        {repo.private
                          ? <Lock size={12} style={{ color: "var(--text-disabled)", flexShrink: 0 }} />
                          : <Globe size={12} style={{ color: "var(--text-disabled)", flexShrink: 0 }} />
                        }
                        <span className="repo-item-name">{repo.full_name}</span>
                        {repo.language && (
                          <span className="repo-item-badge" style={{ marginLeft: "auto" }}>{repo.language}</span>
                        )}
                      </div>
                      {repo.description && (
                        <div className="repo-item-desc">{repo.description}</div>
                      )}
                      <div className="repo-item-meta">
                        <span>{t("create.last.commit", { date: formatRelativeDate(repo.updated_at) })}</span>
                        {repo.fork && <span className="repo-item-badge">Fork</span>}
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
            {selectedRepos.length > 0 && (
              <div style={{ fontSize: 12, color: "var(--brand-500)", marginTop: 6 }}>
                {t("create.repos.selected.list", { n: selectedRepos.length, list: selectedRepos.join(", ") })}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

/* ── Step 2 ── */
function StepClouds({ form, setForm, setAzureAllowedRegions, t }) {
  const sourceCloud = CLOUDS.find((c) => c.value === form.source_cloud);
  const targetCloud = CLOUDS.find((c) => c.value === form.target_cloud);

  return (
    <div>
      <h3 className="wizard-step-title">{t("create.step2.title")}</h3>
      <p className="wizard-step-desc">{t("create.step2.desc")}</p>

      <div className="form-grid">
        <CloudSelector
          label={t("create.source.cloud.label")}
          value={form.source_cloud}
          onChange={(v) => setForm((f) => ({ ...f, source_cloud: v }))}
          otherValue={form.target_cloud}
          otherSideTitle={t("create.cloud.other.side")}
        />
        <CloudSelector
          label={t("create.target.cloud.label")}
          value={form.target_cloud}
          onChange={(v) => { setForm((f) => ({ ...f, target_cloud: v, target_region: "" })); setAzureAllowedRegions(null); }}
          otherValue={form.source_cloud}
          otherSideTitle={t("create.cloud.other.side")}
        />
      </div>

      <div className="cloud-migration-visual">
        <div className={`cloud-box ${form.source_cloud}`}>
          <Cloud size={22} />
          <span>{form.source_cloud.toUpperCase()}</span>
          {sourceCloud && <span style={{ fontSize: 11, opacity: 0.7 }}>{sourceCloud.label}</span>}
        </div>
        <div className="migration-arrow">
          <div className="arrow-line" />
          <span className="arrow-label">{t("create.ai.migration.label")}</span>
        </div>
        <div className={`cloud-box ${form.target_cloud}`}>
          <Cloud size={22} />
          <span>{form.target_cloud.toUpperCase()}</span>
          {targetCloud && <span style={{ fontSize: 11, opacity: 0.7 }}>{targetCloud.label}</span>}
        </div>
      </div>

      {form.source_cloud === form.target_cloud && (
        <div className="alert alert-warning" style={{ marginTop: 16 }}>
          {t("create.clouds.diff")}
        </div>
      )}
    </div>
  );
}

/* ── Step 3 ── */
function StepCredentials({ form, awsCreds, setAwsCreds, gcpCreds, setGcpCreds,
  azureCreds, setAzureCreds, cloudCredsValidation, setCloudCredsValidation,
  handleCloudCredsValidate, t }) {

  const cloud = form.target_cloud;

  return (
    <div>
      <h3 className="wizard-step-title">{t("create.step3.title")}</h3>
      <p className="wizard-step-desc">
        {t("create.step3.desc.prefix")} <strong>{cloud.toUpperCase()}</strong> {t("create.step3.desc.suffix")}
      </p>

      <div className="form-grid">
        {cloud === "aws" && (
          <>
            <div className="form-group">
              <label className="form-label">{t("create.aws.access.key")}</label>
              <input className="form-input" placeholder="YOUR_AWS_ACCESS_KEY"
                value={awsCreds.access_key}
                onChange={(e) => { setAwsCreds((p) => ({ ...p, access_key: e.target.value })); setCloudCredsValidation(null); }}
              />
            </div>
            <div className="form-group">
              <label className="form-label">{t("create.aws.secret.key")}</label>
              <input className="form-input" type="password" placeholder="wJalrXUtnFEMI/…"
                value={awsCreds.secret_key}
                onChange={(e) => { setAwsCreds((p) => ({ ...p, secret_key: e.target.value })); setCloudCredsValidation(null); }}
              />
            </div>
            <div className="form-group col-full">
              <label className="form-label">
                {t("create.aws.session.token")} <span className="form-label-optional">{t("create.aws.session.optional")}</span>
              </label>
              <input className="form-input" type="password" placeholder="FwoGZXIvYXdzE…"
                value={awsCreds.session_token}
                onChange={(e) => { setAwsCreds((p) => ({ ...p, session_token: e.target.value })); setCloudCredsValidation(null); }}
              />
            </div>
          </>
        )}

        {cloud === "gcp" && (
          <>
            <div className="form-group">
              <label className="form-label">{t("create.gcp.project")}</label>
              <input className="form-input" placeholder="my-gcp-project-123"
                value={gcpCreds.project_id}
                onChange={(e) => { setGcpCreds((p) => ({ ...p, project_id: e.target.value })); setCloudCredsValidation(null); }}
              />
            </div>
            <div className="form-group col-full">
              <label className="form-label">{t("create.gcp.sa.json")}</label>
              <textarea
                className="form-input form-textarea"
                rows={5}
                placeholder={'{\n  "type": "service_account",\n  "project_id": "...",\n  ...\n}'}
                value={gcpCreds.service_account_json}
                onChange={(e) => { setGcpCreds((p) => ({ ...p, service_account_json: e.target.value })); setCloudCredsValidation(null); }}
                style={{ fontFamily: "var(--font-mono)", fontSize: 12 }}
              />
              <span className="form-hint">{t("create.gcp.sa.hint")}</span>
            </div>
          </>
        )}

        {cloud === "azure" && (
          <>
            <div className="form-group">
              <label className="form-label">{t("create.azure.tenant")}</label>
              <input className="form-input" placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
                value={azureCreds.tenant_id}
                onChange={(e) => { setAzureCreds((p) => ({ ...p, tenant_id: e.target.value })); setCloudCredsValidation(null); }}
              />
            </div>
            <div className="form-group">
              <label className="form-label">{t("create.azure.client")}</label>
              <input className="form-input" placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
                value={azureCreds.client_id}
                onChange={(e) => { setAzureCreds((p) => ({ ...p, client_id: e.target.value })); setCloudCredsValidation(null); }}
              />
            </div>
            <div className="form-group">
              <label className="form-label">{t("create.azure.secret")}</label>
              <input className="form-input" type="password" placeholder="~Xxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
                value={azureCreds.client_secret}
                onChange={(e) => { setAzureCreds((p) => ({ ...p, client_secret: e.target.value })); setCloudCredsValidation(null); }}
              />
            </div>
            <div className="form-group">
              <label className="form-label">{t("create.azure.subscription")}</label>
              <input className="form-input" placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
                value={azureCreds.subscription_id}
                onChange={(e) => { setAzureCreds((p) => ({ ...p, subscription_id: e.target.value })); setCloudCredsValidation(null); }}
              />
            </div>
          </>
        )}

        <div className="form-group col-full" style={{ display: "flex", gap: 12, alignItems: "center" }}>
          <button
            type="button"
            className="btn btn-secondary"
            onClick={handleCloudCredsValidate}
            disabled={cloudCredsValidation === "loading"}
          >
            {cloudCredsValidation === "loading"
              ? <><span className="spin-inline dark" /> {t("create.creds.verifying")}</>
              : t("create.creds.verify.btn", { cloud: cloud.toUpperCase() })
            }
          </button>
          <ValidationBadge state={cloudCredsValidation} />
        </div>

        {cloudCredsValidation && cloudCredsValidation !== "loading" && !cloudCredsValidation.valid && (
          <div className="form-group col-full">
            <div className="alert alert-error" style={{ marginBottom: 0 }}>
              {cloudCredsValidation.detail || cloudCredsValidation.error || t("create.creds.invalid")}
            </div>
          </div>
        )}

        <div className="form-group col-full">
          <div className="creds-warning">
            <Shield size={16} style={{ flexShrink: 0, marginTop: 1 }} />
            <span>{t("create.creds.security.note")}</span>
          </div>
        </div>
      </div>
    </div>
  );
}

/* ── Step 4 ── */
function StepConstraints({ form, setForm, azureAllowedRegions, t }) {
  const set = (key) => (e) => setForm((f) => ({ ...f, [key]: e.target.value }));

  const regionOptions = (() => {
    if (form.target_cloud === "azure" && azureAllowedRegions && azureAllowedRegions.length > 0) {
      return [{ value: "", labelKey: "create.region.no.pref" }, ...azureAllowedRegions];
    }
    return REGIONS_BY_CLOUD[form.target_cloud] || [];
  })();

  const COMPLIANCE_OPTIONS = [
    { value: "GDPR",    label: "GDPR",     descKey: "create.compliance.gdpr.desc" },
    { value: "HIPAA",   label: "HIPAA",    descKey: "create.compliance.hipaa.desc" },
    { value: "PCI-DSS", label: "PCI-DSS",  descKey: "create.compliance.pci.desc" },
    { value: "ISO27001",label: "ISO 27001",descKey: "create.compliance.iso.desc" },
    { value: "SOC2",    label: "SOC2",     descKey: "create.compliance.soc2.desc" },
  ];

  const RESIDENCIES = [
    { value: "",                   labelKey: "create.residency.none" },
    { value: "Union Europeenne",   labelKey: "create.residency.eu" },
    { value: "Strictement France", labelKey: "create.residency.fr" },
    { value: "US",                 labelKey: "create.residency.us" },
  ];

  const toggleCompliance = (value) => {
    setForm((f) => {
      const current = f.compliance_standards || [];
      const next = current.includes(value)
        ? current.filter((v) => v !== value)
        : [...current, value];
      return { ...f, compliance_standards: next };
    });
  };

  return (
    <div>
      <h3 className="wizard-step-title">{t("create.step4.title")}</h3>
      <p className="wizard-step-desc">{t("create.step4.desc")}</p>

      <div className="form-grid">

        {/* Budget */}
        <div className="form-group">
          <label className="form-label">
            <DollarSign size={13} /> {t("create.budget.max.label")}
          </label>
          <input
            className="form-input"
            type="number" min="0" step="0.01"
            placeholder={t("create.budget.unlimited")}
            value={form.budget_max_mensuel}
            onChange={set("budget_max_mensuel")}
          />
          <span className="form-hint">{t("create.budget.hint.agent")}</span>
        </div>

        {/* Target Region */}
        <div className="form-group">
          <label className="form-label" style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <MapPin size={13} /> {t("create.region.label2")}
            {form.target_cloud === "azure" && azureAllowedRegions && (
              <span style={{
                fontSize: 10, fontWeight: 600, padding: "1px 7px", borderRadius: 8,
                background: "#f0fdf4", color: "#16a34a", border: "1px solid #bbf7d0",
              }}>
                {t("create.region.azure.allowed", { n: azureAllowedRegions.length })}
              </span>
            )}
          </label>
          <select className="form-select" value={form.target_region} onChange={set("target_region")}>
            {regionOptions.map((r) => (
              <option key={r.value} value={r.value}>{r.labelKey ? t(r.labelKey) : r.label}</option>
            ))}
          </select>
          <span className="form-hint">
            {form.target_cloud === "azure" && azureAllowedRegions
              ? t("create.region.hint.azure")
              : t("create.region.hint.default")}
          </span>
        </div>

        {/* Data Residency */}
        <div className="form-group">
          <label className="form-label">
            <MapPin size={13} /> {t("create.residency.label2")}
          </label>
          <select className="form-select" value={form.data_residency_requirement} onChange={set("data_residency_requirement")}>
            {RESIDENCIES.map((r) => <option key={r.value} value={r.value}>{t(r.labelKey)}</option>)}
          </select>
          <span className="form-hint">{t("create.residency.hint2")}</span>
        </div>

        {/* Compliance Standards */}
        <div className="form-group col-full">
          <label className="form-label">
            <Shield size={13} /> {t("create.compliance.label")}
            <span className="form-label-optional"> {t("create.compliance.optional")}</span>
          </label>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 10, marginTop: 8 }}>
            {COMPLIANCE_OPTIONS.map((opt) => {
              const checked = (form.compliance_standards || []).includes(opt.value);
              return (
                <label key={opt.value} style={{
                  display: "flex", alignItems: "flex-start", gap: 10, cursor: "pointer",
                  padding: "10px 14px", borderRadius: 8, border: `1.5px solid ${checked ? "var(--primary)" : "var(--border)"}`,
                  background: checked ? "var(--primary-light, #eff6ff)" : "var(--surface)",
                  minWidth: 180, flex: "1 1 180px",
                }}>
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={() => toggleCompliance(opt.value)}
                    style={{ marginTop: 2, accentColor: "var(--primary)", flexShrink: 0 }}
                  />
                  <div>
                    <div style={{ fontWeight: 600, fontSize: 13 }}>{opt.label}</div>
                    <div style={{ fontSize: 11, color: "var(--text-secondary)", marginTop: 2 }}>{t(opt.descKey)}</div>
                  </div>
                </label>
              );
            })}
          </div>
          <span className="form-hint" style={{ marginTop: 6 }}>{t("create.compliance.hint")}</span>
        </div>

        {/* High Availability */}
        <div className="form-group">
          <label className="form-label">{t("create.ha.label")}</label>
          <div style={{ display: "flex", gap: 10, marginTop: 6 }}>
            {[false, true].map((val) => (
              <label key={String(val)} style={{
                display: "flex", alignItems: "center", gap: 8, cursor: "pointer",
                padding: "8px 16px", borderRadius: 8,
                border: `1.5px solid ${form.high_availability_required === val ? "var(--primary)" : "var(--border)"}`,
                background: form.high_availability_required === val ? "var(--primary-light, #eff6ff)" : "var(--surface)",
                fontWeight: form.high_availability_required === val ? 600 : 400, fontSize: 13,
              }}>
                <input
                  type="radio"
                  name="ha_required"
                  checked={form.high_availability_required === val}
                  onChange={() => setForm((f) => ({ ...f, high_availability_required: val }))}
                  style={{ accentColor: "var(--primary)" }}
                />
                {val ? t("create.ha.required") : t("create.ha.not.required")}
              </label>
            ))}
          </div>
          <span className="form-hint">
            {form.high_availability_required ? t("create.ha.hint.on") : t("create.ha.hint.off")}
          </span>
        </div>

        {/* Network Isolation */}
        <div className="form-group">
          <label className="form-label">{t("create.network.label")}</label>
          <div style={{ display: "flex", gap: 10, marginTop: 6 }}>
            {[false, true].map((val) => (
              <label key={String(val)} style={{
                display: "flex", alignItems: "center", gap: 8, cursor: "pointer",
                padding: "8px 16px", borderRadius: 8,
                border: `1.5px solid ${form.network_isolation_required === val ? "var(--primary)" : "var(--border)"}`,
                background: form.network_isolation_required === val ? "var(--primary-light, #eff6ff)" : "var(--surface)",
                fontWeight: form.network_isolation_required === val ? 600 : 400, fontSize: 13,
              }}>
                <input
                  type="radio"
                  name="network_isolation"
                  checked={form.network_isolation_required === val}
                  onChange={() => setForm((f) => ({ ...f, network_isolation_required: val }))}
                  style={{ accentColor: "var(--primary)" }}
                />
                {val ? t("create.network.isolated") : t("create.network.public")}
              </label>
            ))}
          </div>
          <span className="form-hint">
            {form.network_isolation_required ? t("create.network.hint.on") : t("create.network.hint.off")}
          </span>
        </div>

      </div>
    </div>
  );
}

/* Parse response JSON safely */
async function safeJson(res) {
  try {
    const text = await res.text();
    if (!text) return { valid: false, error: "Server returned an empty response. The API may still be starting." };
    return JSON.parse(text);
  } catch {
    return { valid: false, error: "Server returned an invalid response. The API may still be starting." };
  }
}

/* ══════════════════════════════════════════════════════════════════════════
   MAIN COMPONENT
   ══════════════════════════════════════════════════════════════════════════ */
export default function CreateMigration() {
  const navigate = useNavigate();
  const { t } = useI18n();

  const WIZARD_STEPS = [
    { id: "source",      label: t("create.wizard.step.source"),      icon: GitBranch },
    { id: "clouds",      label: t("create.wizard.step.clouds"),      icon: Cloud     },
    { id: "credentials", label: t("create.wizard.step.creds"),       icon: Key       },
    { id: "constraints", label: t("create.wizard.step.constraints"), icon: Shield    },
  ];

  const [step, setStep]       = useState(0);
  const [direction, setDir]   = useState(1);
  const [loading, setLoading]           = useState(false);
  const [loadingRepos, setLoadingRepos] = useState(false);
  const [error, setError]               = useState(null);
  const [notice, setNotice]             = useState(null);
  const [availableRepos, setAvailableRepos]   = useState([]);
  const [selectedRepos, setSelectedRepos]     = useState([]);
  const [tokenValidation, setTokenValidation]           = useState(null);
  const [cloudCredsValidation, setCloudCredsValidation] = useState(null);

  const [form, setForm] = useState({
    repo_url:                    "",
    repo_urls:                   [],
    github_url:                  "",
    github_token:                "",
    source_cloud:                "aws",
    target_cloud:                "gcp",
    budget_max_mensuel:          "",
    data_residency_requirement:  "",
    target_region:               "",
    compliance_standards:        [],
    high_availability_required:  false,
    network_isolation_required:  false,
  });

  const [awsCreds,   setAwsCreds]   = useState({ access_key: "", secret_key: "", session_token: "" });
  const [gcpCreds,   setGcpCreds]   = useState({ project_id: "", service_account_json: "" });
  const [azureCreds, setAzureCreds] = useState({ tenant_id: "", client_id: "", client_secret: "", subscription_id: "" });
  const [azureAllowedRegions, setAzureAllowedRegions] = useState(null);

  const handleTokenBlur = useCallback(async () => {
    const token = form.github_token.trim();
    if (!token) { setTokenValidation(null); return; }
    setTokenValidation("loading");
    try {
      const res = await fetch("/api/v1/credentials/validate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          provider: "github",
          credentials: { token, repo: form.repo_url.replace("https://github.com/", "") || undefined },
        }),
      });
      setTokenValidation(await safeJson(res));
    } catch {
      setTokenValidation({ valid: false, error: "Network error" });
    }
  }, [form.github_token, form.repo_url]);

  const handleCloudCredsValidate = async () => {
    const cloud = form.target_cloud;
    let credentials = {};
    if (cloud === "aws") {
      if (!awsCreds.access_key || !awsCreds.secret_key) {
        setCloudCredsValidation({ valid: false, error: "access_key and secret_key are required" });
        return;
      }
      credentials = { access_key: awsCreds.access_key, secret_key: awsCreds.secret_key,
        region: form.target_region || "us-east-1",
        ...(awsCreds.session_token ? { session_token: awsCreds.session_token } : {}) };
    } else if (cloud === "gcp") {
      if (!gcpCreds.project_id || !gcpCreds.service_account_json) {
        setCloudCredsValidation({ valid: false, error: "project_id and service account JSON are required" });
        return;
      }
      let sa_json;
      try { sa_json = JSON.parse(gcpCreds.service_account_json); }
      catch { setCloudCredsValidation({ valid: false, error: "Invalid service account JSON" }); return; }
      credentials = { project_id: gcpCreds.project_id, service_account_json: sa_json };
    } else if (cloud === "azure") {
      const { tenant_id, client_id, client_secret, subscription_id } = azureCreds;
      if (!tenant_id || !client_id || !client_secret || !subscription_id) {
        setCloudCredsValidation({ valid: false, error: "All Azure fields are required" });
        return;
      }
      credentials = { tenant_id, client_id, client_secret, subscription_id };
    }
    setCloudCredsValidation("loading");
    try {
      const res = await fetch("/api/v1/credentials/validate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ provider: cloud, credentials }),
      });
      const data = await safeJson(res);
      setCloudCredsValidation(data);
      if (cloud === "azure" && data.valid) {
        if (data.allowed_regions && data.allowed_regions.length > 0) {
          setAzureAllowedRegions(data.allowed_regions);
        } else {
          try {
            const regRes = await fetch("/api/v1/credentials/azure/regions", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify(credentials),
            });
            const regData = await safeJson(regRes);
            if (regData.regions && regData.regions.length > 0) {
              setAzureAllowedRegions(regData.regions);
            }
          } catch { /* Non-fatal */ }
        }
      }
    } catch {
      setCloudCredsValidation({ valid: false, error: "Network error" });
    }
  };

  const handleLoadRepos = async () => {
    if (!form.github_token || !form.github_url) {
      setError(t("create.error.repos"));
      return;
    }
    setLoadingRepos(true);
    setError(null);
    setNotice(null);
    try {
      const result = await fetchGithubRepos(form.github_token, form.github_url);
      const repos = result?.repos || [];
      setAvailableRepos(repos);
      setSelectedRepos([]);
      setNotice(t("create.notice.repos.loaded", { n: repos.length }));
    } catch (err) {
      setError(err?.message || t("create.error.load.repos"));
    } finally {
      setLoadingRepos(false);
    }
  };

  const toggleRepoSelection = (fullName) => {
    const next = selectedRepos.includes(fullName)
      ? selectedRepos.filter((r) => r !== fullName)
      : [...selectedRepos, fullName];
    setSelectedRepos(next);
    if (next.length > 0) {
      setForm((f) => ({
        ...f,
        repo_url:  `https://github.com/${next[0]}`,
        repo_urls: next.map((r) => `https://github.com/${r}`),
      }));
    } else {
      setForm((f) => ({ ...f, repo_url: "", repo_urls: [] }));
    }
  };

  // Calcule en temps réel si l'étape courante est valide (sans déclencher d'erreur)
  const stepBlockers = (() => {
    const msgs = [];
    if (step === 0) {
      if (!form.github_token.trim())
        msgs.push("Token GitHub obligatoire");
      if (!form.repo_url.trim() && (!form.repo_urls || form.repo_urls.length === 0))
        msgs.push("URL du dépôt obligatoire");
      if (tokenValidation && tokenValidation !== "loading" && !tokenValidation.valid)
        msgs.push("Token GitHub invalide — vérifiez-le");
    }
    if (step === 1) {
      if (form.source_cloud === form.target_cloud)
        msgs.push("Cloud source et destination doivent être différents");
    }
    if (step === 2) {
      const cloud = form.target_cloud;
      if (cloud === "aws") {
        if (!awsCreds.access_key.trim()) msgs.push("AWS Access Key obligatoire");
        if (!awsCreds.secret_key.trim()) msgs.push("AWS Secret Key obligatoire");
      }
      if (cloud === "gcp") {
        if (!gcpCreds.project_id.trim()) msgs.push("GCP Project ID obligatoire");
        if (!gcpCreds.service_account_json.trim()) msgs.push("GCP Service Account JSON obligatoire");
      }
      if (cloud === "azure") {
        const { tenant_id, client_id, client_secret, subscription_id } = azureCreds;
        if (!tenant_id.trim())       msgs.push("Azure Tenant ID obligatoire");
        if (!client_id.trim())       msgs.push("Azure Client ID obligatoire");
        if (!client_secret.trim())   msgs.push("Azure Client Secret obligatoire");
        if (!subscription_id.trim()) msgs.push("Azure Subscription ID obligatoire");
      }
    }
    return msgs;
  })();

  const canProceed = stepBlockers.length === 0;

  const goNext = () => {
    if (!canProceed) {
      setError(stepBlockers[0]);
      return;
    }
    setError(null);
    setDir(1);
    setStep((s) => s + 1);
  };

  const goBack = () => {
    setError(null);
    setDir(-1);
    setStep((s) => s - 1);
  };

  const credsAreValid = cloudCredsValidation && cloudCredsValidation !== "loading" && cloudCredsValidation.valid;

  const handleSubmit = async (e) => {
    e.preventDefault();
    const hasRepos = (form.repo_urls && form.repo_urls.length > 0) || form.repo_url;
    if (!hasRepos || !form.github_token || !form.source_cloud) {
      setError(t("create.error.required"));
      return;
    }
    if (form.source_cloud === form.target_cloud) {
      setError(t("create.error.same.cloud"));
      return;
    }

    if (!tokenValidation || tokenValidation === "loading") {
      setTokenValidation("loading");
      try {
        const res = await fetch("/api/v1/credentials/validate", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            provider: "github",
            credentials: { token: form.github_token.trim(),
              repo: form.repo_url.replace("https://github.com/", "") || undefined },
          }),
        });
        const data = await safeJson(res);
        setTokenValidation(data);
        if (!data.valid) { setError(t("create.error.invalid.token")); return; }
      } catch {
        setTokenValidation({ valid: false, error: "Network error" });
        setError(t("create.error.network.token"));
        return;
      }
    } else if (!tokenValidation.valid) {
      setError(t("create.error.invalid.token"));
      return;
    }

    if (!credsAreValid) {
      const ok = window.confirm(t("create.confirm.no.creds"));
      if (!ok) return;
    }

    setLoading(true);
    setError(null);
    try {
      const repoUrls = form.repo_urls?.length > 0 ? form.repo_urls : null;
      const payload = {
        repo_url:                  form.repo_url || (repoUrls?.[0]) || "",
        repo_urls:                 repoUrls?.length > 1 ? repoUrls : undefined,
        github_token:              form.github_token,
        source_cloud:              form.source_cloud,
        target_cloud:              form.target_cloud,
        credentials_pre_validated: false,
      };
      if (form.budget_max_mensuel)                      payload.monthly_budget_usd         = parseFloat(form.budget_max_mensuel);
      if (form.data_residency_requirement)              payload.data_residency_requirement = form.data_residency_requirement;
      if (form.target_region)                           payload.target_region              = form.target_region;
      if (form.compliance_standards?.length)            payload.compliance_standards        = form.compliance_standards;
      payload.high_availability_required  = Boolean(form.high_availability_required);
      payload.network_isolation_required  = Boolean(form.network_isolation_required);
      const result = await createMigration(payload);
      await storeCredentials(result.id, "github", { token: form.github_token });

      if (credsAreValid) {
        let cloudCreds = {};
        if (form.target_cloud === "aws") {
          cloudCreds = { ...awsCreds };
        } else if (form.target_cloud === "gcp") {
          let sa_json = gcpCreds.service_account_json;
          try { sa_json = JSON.parse(gcpCreds.service_account_json); } catch (_) {}
          cloudCreds = { project_id: gcpCreds.project_id, service_account_json: sa_json };
        } else if (form.target_cloud === "azure") {
          cloudCreds = { ...azureCreds };
        }
        try {
          await storeCredentials(result.id, form.target_cloud, cloudCreds);
          await updateMigration(result.id, { credentials_pre_validated: true });
        } catch (storeErr) {
          setError(t("create.error.vault", {
            cloud: form.target_cloud.toUpperCase(),
            err: storeErr?.message || "vault error",
          }));
          await new Promise((r) => setTimeout(r, 5000));
        }
      }

      navigate(`/migrations/${result.id}`);
    } catch (err) {
      const msg =
        typeof err === "string" ? err :
        err?.response?.data?.detail
          ? (Array.isArray(err.response.data.detail)
              ? err.response.data.detail.map((e) => `${e.loc?.[1]}: ${e.msg}`).join("\n")
              : err.response.data.detail)
          : err?.message || t("common.error");
      setError(msg);
    } finally {
      setLoading(false);
    }
  };

  const isLastStep = step === WIZARD_STEPS.length - 1;

  return (
    <motion.div
      className="page wizard-shell"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.18 }}
    >
      <div className="page-header">
        <div>
          <button className="btn btn-secondary btn-sm" onClick={() => navigate("/dashboard")}>
            <ArrowLeft size={14} /> {t("create.back.btn")}
          </button>
          <h2 className="page-title" style={{ marginTop: 10 }}>{t("create.title")}</h2>
          <p className="page-subtitle">{t("create.wizard.subtitle")}</p>
        </div>
      </div>

      {error  && <Alert type="error"   onClose={() => setError(null)}>{error}</Alert>}
      {notice && <Alert type="success" onClose={() => setNotice(null)}>{notice}</Alert>}

      <WizardProgress current={step} steps={WIZARD_STEPS} />

      <form onSubmit={handleSubmit}>
        <div className="wizard-body">
          <AnimatePresence mode="wait" custom={direction}>
            <motion.div
              key={step}
              custom={direction}
              variants={slideVariants}
              initial="enter"
              animate="center"
              exit="exit"
              transition={{ duration: 0.22, ease: [0.16, 1, 0.3, 1] }}
            >
              {step === 0 && (
                <StepSource
                  form={form} setForm={setForm}
                  tokenValidation={tokenValidation} setTokenValidation={setTokenValidation}
                  handleTokenBlur={handleTokenBlur}
                  availableRepos={availableRepos} loadingRepos={loadingRepos}
                  handleLoadRepos={handleLoadRepos}
                  selectedRepos={selectedRepos} toggleRepoSelection={toggleRepoSelection}
                  t={t}
                />
              )}
              {step === 1 && (
                <StepClouds form={form} setForm={setForm} setAzureAllowedRegions={setAzureAllowedRegions} t={t} />
              )}
              {step === 2 && (
                <StepCredentials
                  form={form}
                  awsCreds={awsCreds} setAwsCreds={setAwsCreds}
                  gcpCreds={gcpCreds} setGcpCreds={setGcpCreds}
                  azureCreds={azureCreds} setAzureCreds={setAzureCreds}
                  cloudCredsValidation={cloudCredsValidation}
                  setCloudCredsValidation={setCloudCredsValidation}
                  handleCloudCredsValidate={handleCloudCredsValidate}
                  t={t}
                />
              )}
              {step === 3 && (
                <StepConstraints form={form} setForm={setForm} azureAllowedRegions={azureAllowedRegions} t={t} />
              )}
            </motion.div>
          </AnimatePresence>
        </div>

        <div className="wizard-actions">
          <div>
            {step > 0 && (
              <button type="button" className="btn btn-secondary" onClick={goBack}>
                <ArrowLeft size={14} /> {t("create.back.btn")}
              </button>
            )}
          </div>

          <div className="wizard-actions-right">
            <button type="button" className="btn btn-secondary" onClick={() => navigate("/dashboard")}>
              {t("create.cancel")}
            </button>

            {!isLastStep ? (
              <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 4 }}>
                <button
                  type="button"
                  className="btn btn-primary"
                  onClick={goNext}
                  disabled={!canProceed}
                  title={!canProceed ? stepBlockers.join(" · ") : ""}
                  style={{ opacity: canProceed ? 1 : 0.5, cursor: canProceed ? "pointer" : "not-allowed" }}
                >
                  {t("create.next.btn")} <ChevronRight size={15} />
                </button>
                {!canProceed && (
                  <span style={{ fontSize: 11, color: "var(--error-solid, #dc2626)", maxWidth: 260, textAlign: "right", lineHeight: 1.3 }}>
                    {stepBlockers[0]}
                  </span>
                )}
              </div>
            ) : (
              <>
                {!credsAreValid && (
                  <div className="creds-warning" style={{ alignSelf: "center", marginRight: 4, padding: "6px 12px" }}>
                    <Shield size={13} />
                    <span style={{ fontSize: 12 }}>{t("create.creds.not.validated")}</span>
                  </div>
                )}
                <button type="submit" className="btn btn-primary btn-lg" disabled={loading}>
                  {loading
                    ? <><span className="spin-inline" /> {t("create.creating")}</>
                    : <><Rocket size={16} /> {t("create.create.btn")}</>
                  }
                </button>
              </>
            )}
          </div>
        </div>
      </form>
    </motion.div>
  );
}

