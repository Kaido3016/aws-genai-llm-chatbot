import { Box, ExpandableSection, Link, SpaceBetween } from "@cloudscape-design/components";
import { Citation } from "./types";

interface ChatMessageCitationsProps {
  citations?: Citation[];
}

/**
 * Defense-in-depth, client-side mirror of the server's URL-scheme check
 * (genai_core.citations._extract_safe_source_url). The backend already
 * guarantees sourceUrl is either null or a genuine http(s) URL, but this
 * component does not take that on faith — if sourceUrl were ever
 * something else (a future backend regression, or legacy/malformed
 * stored data), rendering it unchecked as an href could still enable a
 * javascript:/data: URI to execute on click. This keeps the same
 * "belt and suspenders" posture used server-side.
 */
function isSafeHttpUrl(url: string): boolean {
  try {
    const parsed = new URL(url);
    return parsed.protocol === "http:" || parsed.protocol === "https:";
  } catch {
    return false;
  }
}

/**
 * A3 — user-facing "Sources" section, shown to every authenticated user
 * (not gated by admin/showMetadata, unlike the debug JSON panel this
 * file is deliberately kept separate from).
 *
 * This component only ever renders the safe, redacted fields the backend
 * (genai_core.citations.build_safe_citations /
 * reapply_citation_allowlist — see lib/shared/layers/python-sdk/python/
 * genai_core/citations.py) has already allow-listed: documentTitle,
 * sourceUrl (only for genuinely public website/rssfeed sources),
 * snippet, citationIndex. It never receives or renders workspace_id,
 * document_id, chunk_id, scores, model config, or prompts — those stay
 * confined to the existing admin-only metadata panel in chat-message.tsx.
 *
 * React escapes text-node content by default (no `dangerouslySetInnerHTML`
 * is used here), so this is a second, redundant safety layer on top of
 * the server already HTML-escaping snippet/title text — consistent with
 * the "belt and suspenders" approach used throughout A2/A2.1/A3.
 */
export function ChatMessageCitations({ citations }: ChatMessageCitationsProps) {
  if (!citations || citations.length === 0) return null;

  return (
    <ExpandableSection
      variant="footer"
      headerText={`Sources (${citations.length})`}
    >
      <SpaceBetween size="s">
        {citations.map((citation) => (
          <Box key={citation.citationIndex} padding={{ bottom: "xs" }}>
            <Box fontWeight="bold">
              [{citation.citationIndex}]{" "}
              {citation.sourceUrl && isSafeHttpUrl(citation.sourceUrl) ? (
                <Link external href={citation.sourceUrl}>
                  {citation.documentTitle}
                </Link>
              ) : (
                citation.documentTitle
              )}
            </Box>
            {citation.snippet && (
              <Box color="text-body-secondary" fontSize="body-s">
                {citation.snippet}
              </Box>
            )}
          </Box>
        ))}
      </SpaceBetween>
    </ExpandableSection>
  );
}
