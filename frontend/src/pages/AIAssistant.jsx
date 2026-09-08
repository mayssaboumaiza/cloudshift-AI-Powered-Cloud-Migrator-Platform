import { useState, useRef, useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  Bot, User, Send, RefreshCw, Sparkles,
  Lightbulb, Cloud, Shield, GitBranch, FileCode2,
} from "lucide-react";
import { useI18n } from "../context/I18nContext";

// Lightweight markdown renderer — no external dependency
// Handles: **bold**, `code`, ```blocks```, bullet lists, numbered lists
function MarkdownText({ text }) {
  if (!text) return null;

  const lines = text.split("\n");
  const elements = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];

    // Code block
    if (line.startsWith("```")) {
      const lang = line.slice(3).trim();
      const codeLines = [];
      i++;
      while (i < lines.length && !lines[i].startsWith("```")) {
        codeLines.push(lines[i]);
        i++;
      }
      elements.push(
        <pre key={i} style={{
          background: "#1e293b", color: "#e2e8f0", borderRadius: 8,
          padding: "10px 14px", fontSize: 12, lineHeight: 1.6,
          overflowX: "auto", margin: "8px 0", fontFamily: "var(--font-mono)",
          border: "1px solid #334155",
        }}>
          {codeLines.join("\n")}
        </pre>
      );
      i++;
      continue;
    }

    // Bullet list
    if (line.match(/^[•\-\*] /)) {
      const items = [];
      while (i < lines.length && lines[i].match(/^[•\-\*] /)) {
        items.push(lines[i].replace(/^[•\-\*] /, ""));
        i++;
      }
      elements.push(
        <ul key={i} style={{ margin: "6px 0 6px 16px", padding: 0, listStyle: "none" }}>
          {items.map((item, j) => (
            <li key={j} style={{ display: "flex", gap: 7, marginBottom: 4, fontSize: 13.5, lineHeight: 1.6 }}>
              <span style={{ color: "var(--brand-500)", flexShrink: 0, marginTop: 2 }}>•</span>
              <span>{renderInline(item)}</span>
            </li>
          ))}
        </ul>
      );
      continue;
    }

    // Numbered list
    if (line.match(/^\d+\. /)) {
      const items = [];
      while (i < lines.length && lines[i].match(/^\d+\. /)) {
        items.push(lines[i].replace(/^\d+\. /, ""));
        i++;
      }
      elements.push(
        <ol key={i} style={{ margin: "6px 0 6px 20px", padding: 0 }}>
          {items.map((item, j) => (
            <li key={j} style={{ marginBottom: 4, fontSize: 13.5, lineHeight: 1.6 }}>
              {renderInline(item)}
            </li>
          ))}
        </ol>
      );
      continue;
    }

    // Empty line → spacing
    if (line.trim() === "") {
      elements.push(<div key={i} style={{ height: 6 }} />);
      i++;
      continue;
    }

    // Normal paragraph
    elements.push(
      <p key={i} style={{ margin: "2px 0", fontSize: 13.5, lineHeight: 1.6 }}>
        {renderInline(line)}
      </p>
    );
    i++;
  }

  return <>{elements}</>;
}

function renderInline(text) {
  // Handle **bold** and `code` inline
  const parts = text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g);
  return parts.map((part, i) => {
    if (part.startsWith("**") && part.endsWith("**")) {
      return <strong key={i} style={{ fontWeight: 700 }}>{part.slice(2, -2)}</strong>;
    }
    if (part.startsWith("`") && part.endsWith("`")) {
      return (
        <code key={i} style={{
          background: "rgba(99,102,241,0.1)", color: "#6366f1",
          padding: "1px 5px", borderRadius: 4, fontSize: "0.9em",
          fontFamily: "var(--font-mono)",
        }}>
          {part.slice(1, -1)}
        </code>
      );
    }
    return part;
  });
}

function TypingDots() {
  return (
    <div style={{ display: "flex", gap: 4, alignItems: "center", padding: "6px 0" }}>
      {[0, 1, 2].map(i => (
        <span key={i} style={{
          width: 7, height: 7, borderRadius: "50%", background: "var(--text-disabled)",
          display: "inline-block",
          animation: `typingBounce 1.2s ease-in-out ${i * 0.18}s infinite`,
        }} />
      ))}
      <style>{`@keyframes typingBounce{0%,80%,100%{transform:translateY(0)}40%{transform:translateY(-5px)}}`}</style>
    </div>
  );
}

function SourcesLabel({ sources, isUser }) {
  const { t } = useI18n();
  return (
    <div style={{
      marginTop: 8, paddingTop: 8,
      borderTop: `1px solid ${isUser ? "rgba(255,255,255,0.25)" : "var(--surface-3)"}`,
      fontSize: 10.5,
      color: isUser ? "rgba(255,255,255,0.75)" : "var(--text-tertiary)",
      display: "flex", alignItems: "center", gap: 4,
    }}>
      <Sparkles size={10} />
      {t("assistant.sources", { n: sources, s: sources !== 1 ? "s" : "" })}
    </div>
  );
}

function Message({ msg, index }) {
  const isUser = msg.role === "user";
  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.18, delay: index * 0.02 }}
      style={{
        display: "flex", gap: 10,
        flexDirection: isUser ? "row-reverse" : "row",
        alignItems: "flex-start",
        marginBottom: 16,
      }}
    >
      {/* Avatar */}
      <div style={{
        width: 32, height: 32, borderRadius: "50%", flexShrink: 0,
        display: "flex", alignItems: "center", justifyContent: "center",
        background: isUser
          ? "linear-gradient(135deg, #6366F1, #0EA5E9)"
          : "linear-gradient(135deg, #7C3AED, #06B6D4)",
        boxShadow: "0 2px 6px rgba(0,0,0,0.12)",
      }}>
        {isUser
          ? <User size={15} color="#fff" strokeWidth={2} />
          : <Bot  size={15} color="#fff" strokeWidth={2} />
        }
      </div>

      {/* Bubble */}
      <div style={{
        maxWidth: "72%",
        padding: "11px 15px",
        borderRadius: isUser ? "14px 4px 14px 14px" : "4px 14px 14px 14px",
        background: isUser
          ? "linear-gradient(135deg, #6366F1, #0EA5E9)"
          : msg.isError
            ? "var(--error-subtle)"
            : "var(--surface-0)",
        border: isUser
          ? "none"
          : msg.isError
            ? "1px solid var(--error-border)"
            : "1px solid var(--surface-4)",
        color: isUser ? "#fff" : msg.isError ? "var(--error-text)" : "var(--text-primary)",
        fontSize: 13.5, lineHeight: 1.6,
        boxShadow: isUser ? "0 2px 8px rgba(99,102,241,0.25)" : "var(--shadow-sm)",
        wordBreak: "break-word",
      }}>
        {isUser
          ? <span style={{ whiteSpace: "pre-wrap" }}>{msg.content}</span>
          : <MarkdownText text={msg.content} />
        }
        {msg.sources > 0 && (
          <SourcesLabel sources={msg.sources} isUser={isUser} />
        )}
      </div>
    </motion.div>
  );
}

export default function AIAssistant() {
  const { t } = useI18n();

  const SUGGESTIONS = [
    { icon: Cloud,     key: "assistant.suggest.1" },
    { icon: GitBranch, key: "assistant.suggest.2" },
    { icon: FileCode2, key: "assistant.suggest.3" },
    { icon: Shield,    key: "assistant.suggest.4" },
    { icon: Cloud,     key: "assistant.suggest.5" },
    { icon: Sparkles,  key: "assistant.suggest.6" },
  ];

  const [messages, setMessages] = useState([
    {
      role: "assistant",
      content: t("assistant.welcome"),
    },
  ]);
  const [input, setInput]     = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError]     = useState(null);
  const endRef                = useRef(null);
  const inputRef              = useRef(null);
  const chatRef               = useRef(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  async function send(text) {
    const q = (text || input).trim();
    if (!q || loading) return;
    setMessages(prev => [...prev, { role: "user", content: q }]);
    setInput("");
    setLoading(true);
    setError(null);

    const history = messages.map(m => ({ role: m.role, content: m.content }));

    try {
      const res = await fetch("/api/v1/graph-rag/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query: q, history }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.error || `HTTP ${res.status}`);
      }
      const data = await res.json();
      setMessages(prev => [
        ...prev,
        { role: "assistant", content: data.answer, sources: data.sources_count },
      ]);
    } catch (e) {
      setError(e.message);
      setMessages(prev => [
        ...prev,
        { role: "assistant", content: `${t("assistant.error.prefix")}${e.message}`, isError: true },
      ]);
    } finally {
      setLoading(false);
      inputRef.current?.focus();
    }
  }

  function clearChat() {
    setMessages([{
      role: "assistant",
      content: t("assistant.reset.msg"),
    }]);
    setError(null);
  }

  const handleKeyDown = e => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  };

  return (
    <motion.div
      className="page"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.18 }}
      style={{ display: "flex", flexDirection: "column", height: "calc(100vh - var(--topbar-height) - 48px)" }}
    >
      {/* Header */}
      <div className="page-header" style={{ marginBottom: 16 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <div style={{
            width: 38, height: 38, borderRadius: 10,
            background: "linear-gradient(135deg, #7C3AED, #06B6D4)",
            display: "flex", alignItems: "center", justifyContent: "center",
            boxShadow: "0 2px 8px rgba(124,58,237,0.3)",
          }}>
            <Bot size={19} color="#fff" strokeWidth={1.8} />
          </div>
          <div>
            <h2 className="page-title" style={{ marginBottom: 0 }}>{t("assistant.title")}</h2>
            <p className="page-subtitle" style={{ marginBottom: 0 }}>
              {t("assistant.subtitle")}
            </p>
          </div>
        </div>
        <div className="page-actions">
          <button className="btn btn-secondary" onClick={clearChat} disabled={loading}>
            <RefreshCw size={14} />
            {t("assistant.reset")}
          </button>
        </div>
      </div>

      {/* Suggestions row (shown only at start) */}
      <AnimatePresence>
        {messages.length <= 1 && (
          <motion.div
            initial={{ opacity: 0, y: -6 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -6 }}
            transition={{ duration: 0.2 }}
            style={{ marginBottom: 16 }}
          >
            <div style={{
              fontSize: 10, fontWeight: 700, textTransform: "uppercase",
              letterSpacing: "0.1em", color: "var(--text-disabled)", marginBottom: 8,
              display: "flex", alignItems: "center", gap: 5,
            }}>
              <Lightbulb size={11} /> {t("assistant.suggest.label")}
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 6 }}>
              {SUGGESTIONS.map((s, i) => (
                <button
                  key={i}
                  onClick={() => send(t(s.key))}
                  disabled={loading}
                  style={{
                    padding: "8px 12px", borderRadius: 9, fontSize: 11.5,
                    textAlign: "left", cursor: loading ? "default" : "pointer",
                    background: "var(--surface-0)", border: "1px solid var(--surface-4)",
                    color: "var(--text-secondary)", transition: "all 130ms",
                    display: "flex", alignItems: "flex-start", gap: 7,
                    lineHeight: 1.45,
                  }}
                  onMouseEnter={e => {
                    if (!loading) {
                      e.currentTarget.style.background = "var(--surface-3)";
                      e.currentTarget.style.borderColor = "var(--brand-200)";
                    }
                  }}
                  onMouseLeave={e => {
                    e.currentTarget.style.background = "var(--surface-0)";
                    e.currentTarget.style.borderColor = "var(--surface-4)";
                  }}
                >
                  <s.icon size={13} style={{ color: "var(--brand-500)", marginTop: 1, flexShrink: 0 }} />
                  {t(s.key)}
                </button>
              ))}
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Chat window */}
      <div
        ref={chatRef}
        style={{
          flex: 1, overflowY: "auto", scrollbarWidth: "thin",
          padding: "16px", borderRadius: 12,
          background: "var(--surface-1)", border: "1px solid var(--surface-4)",
          marginBottom: 12,
        }}
      >
        {messages.map((msg, i) => (
          <Message key={i} msg={msg} index={i} />
        ))}
        {loading && (
          <motion.div
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            style={{ display: "flex", gap: 10, alignItems: "flex-start", marginBottom: 16 }}
          >
            <div style={{
              width: 32, height: 32, borderRadius: "50%", flexShrink: 0,
              display: "flex", alignItems: "center", justifyContent: "center",
              background: "linear-gradient(135deg, #7C3AED, #06B6D4)",
            }}>
              <Bot size={15} color="#fff" strokeWidth={2} />
            </div>
            <div style={{
              padding: "11px 15px", borderRadius: "4px 14px 14px 14px",
              background: "var(--surface-0)", border: "1px solid var(--surface-4)",
            }}>
              <TypingDots />
            </div>
          </motion.div>
        )}
        <div ref={endRef} />
      </div>

      {/* Input area */}
      <div style={{
        display: "flex", gap: 8, alignItems: "flex-end",
        padding: "10px 12px",
        background: "var(--surface-0)", borderRadius: 12,
        border: "1px solid var(--surface-4)",
        boxShadow: "var(--shadow-sm)",
      }}>
        <textarea
          ref={inputRef}
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={loading}
          placeholder={t("assistant.input.placeholder")}
          rows={1}
          style={{
            flex: 1, border: "none", outline: "none", resize: "none",
            background: "transparent", fontSize: 13.5, lineHeight: 1.5,
            color: "var(--text-primary)", fontFamily: "var(--font-ui)",
            maxHeight: 120, overflowY: "auto",
          }}
          onInput={e => {
            e.target.style.height = "auto";
            e.target.style.height = Math.min(e.target.scrollHeight, 120) + "px";
          }}
        />
        <button
          onClick={() => send()}
          disabled={loading || !input.trim()}
          style={{
            width: 36, height: 36, borderRadius: 9, border: "none",
            cursor: loading || !input.trim() ? "default" : "pointer",
            background: loading || !input.trim()
              ? "var(--surface-3)"
              : "linear-gradient(135deg, #6366F1, #0EA5E9)",
            display: "flex", alignItems: "center", justifyContent: "center",
            flexShrink: 0, transition: "all 150ms",
            boxShadow: loading || !input.trim() ? "none" : "0 2px 6px rgba(99,102,241,0.3)",
          }}
        >
          {loading
            ? <RefreshCw size={15} color="var(--text-disabled)" style={{ animation: "spin 1s linear infinite" }} />
            : <Send size={15} color={input.trim() ? "#fff" : "var(--text-disabled)"} />
          }
        </button>
      </div>

      {/* Keyboard hint */}
      <div style={{
        textAlign: "center", fontSize: 10.5, color: "var(--text-disabled)",
        marginTop: 8, letterSpacing: "0.02em",
      }}>
        {t("assistant.hint")}
      </div>
    </motion.div>
  );
}
