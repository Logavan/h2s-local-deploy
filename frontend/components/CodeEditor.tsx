"use client";

import { useEffect, useMemo, useState } from "react";
import CodeMirror from "@uiw/react-codemirror";
import { sql as langSql } from "@codemirror/lang-sql";
import { linter, Diagnostic } from "@codemirror/lint";
import { syntaxHighlighting, defaultHighlightStyle, HighlightStyle } from "@codemirror/language";
import { tags as t } from "@lezer/highlight";
import { validateSQL, ValidationError } from "../utils/sqlValidator";
import { cn } from "../lib/utils"; // Import cn utility

// Explicit SQL keyword highlight style (light theme friendly)
const sqlHighlightStyle = HighlightStyle.define([
  { tag: t.keyword, color: "#0000ff", fontWeight: "bold" },
  { tag: [t.string, t.special(t.string)], color: "#a31515" },
  { tag: t.number, color: "#098658" },
  { tag: t.bool, color: "#0000ff" },
  { tag: t.null, color: "#0000ff", fontWeight: "bold" },
  { tag: t.comment, color: "#008000", fontStyle: "italic" },
  { tag: t.lineComment, color: "#008000", fontStyle: "italic" },
  { tag: t.blockComment, color: "#008000", fontStyle: "italic" },
  { tag: t.operator, color: "#000000" },
  { tag: t.punctuation, color: "#000000" },
  { tag: [t.typeName, t.className], color: "#267f99" },
  { tag: t.standard(t.name), color: "#0000ff" },
  { tag: [t.function(t.variableName), t.function(t.propertyName)], color: "#795e26" },
  { tag: t.variableName, color: "#001080" },
]);

interface SqlEditorProps {
  value: string;
  onChange: (value: string) => void;
  editorHeight?: string; // New prop for dynamic height
  isCollapsed?: boolean; // New prop to control collapse state
}

export default function SqlEditor({ value, onChange, editorHeight = "340px", isCollapsed = false }: SqlEditorProps) {
  const [sql, setSql] = useState<string>(value);
  const [errors, setErrors] = useState<ValidationError[]>([]);

  // Debounce input a bit
  const debouncedSql = useDebounce(sql, 250);

  useEffect(() => {
    setSql(value); // Update internal state when external value prop changes
  }, [value]);

  useEffect(() => {
    // Call validateSQL with suppressErrors set to true
    const result = validateSQL(debouncedSql, true);
    // Since errors are suppressed, we always treat it as valid for display purposes
    // The validator will return the SQL string if successful or suppressed, or null if not.
    // However, with suppressErrors: true, it will always return the SQL string.
    setErrors([]); // Always clear errors to prevent display
  }, [debouncedSql]);

  const sqlLinter = useMemo(() => {
    return linter((view) => {
      const diagnostics: Diagnostic[] = [];
      // Since errors are suppressed, the linter should not display any diagnostics
      return diagnostics;
    });
  }, []); // No dependencies needed as errors are always suppressed

  return (
    <div className="space-y-2 h-full flex flex-col"> {/* Ensure the container takes full height and is a flex column */}
      <div className={cn("border rounded overflow-hidden", isCollapsed && "h-0 overflow-hidden", !isCollapsed && "h-full sm:h-auto min-h-[200px] sm:min-h-0")}> {/* Hide content when collapsed */}
        {!isCollapsed && ( // Only render CodeMirror if not collapsed
          <div className="h-full sm:h-[340px] overflow-auto">
            <CodeMirror
              value={sql}
              height="100%" // Use percentage height within the container
              theme="light" // Use 'light' theme for CodeMirror as requested
              extensions={[langSql(), syntaxHighlighting(sqlHighlightStyle), syntaxHighlighting(defaultHighlightStyle, { fallback: true }), sqlLinter]}
              onChange={(v) => {
                setSql(v);
                onChange(v); // Propagate changes up
              }}
            />
          </div>
        )}
      </div>

      {/* Removed conditional message display as per user request */}
    </div>
  );
}

/* Small debounce hook */
function useDebounce<T>(value: T, delay = 250) {
  const [v, setV] = useState(value);
  useEffect(() => {
    const id = setTimeout(() => setV(value), delay);
    return () => clearTimeout(id);
  }, [value, delay]);
  return v;
}
