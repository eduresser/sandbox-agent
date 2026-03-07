import { useRef, useEffect, useState, memo, useMemo } from "react";
import type { DisplayOutput } from "../types";
import { ClickableImage } from "./ImageLightbox";

/**
 * Renders a single DisplayOutput based on its MIME type.
 * Memoized to prevent re-renders from interrupting media playback.
 */
export const DisplayOutputItem = memo(function DisplayOutputItem({ output }: { output: DisplayOutput }) {
  const { type, data } = output;

  if (type.startsWith("image/")) {
    const src = `data:${type};base64,${data}`;
    return (
      <ClickableImage
        src={src}
        alt="Output"
        className="max-w-full rounded-lg border border-zinc-800"
      />
    );
  }

  if (type === "text/html" || type === "text/svg+xml" || type === "image/svg+xml") {
    return <HtmlOutput html={data} />;
  }

  if (type.startsWith("audio/")) {
    const src = `data:${type};base64,${data}`;
    return <audio controls preload="metadata" src={src} className="w-full rounded-lg" />;
  }

  if (type.startsWith("video/")) {
    const src = `data:${type};base64,${data}`;
    return (
      <video controls preload="metadata" src={src} className="max-w-full rounded-lg border border-zinc-800" />
    );
  }

  return null;
}, (prev, next) => prev.output.type === next.output.type && prev.output.data === next.output.data);

/**
 * Renders a list of DisplayOutput items.
 */
export function DisplayOutputList({ outputs }: { outputs: DisplayOutput[] }) {
  if (outputs.length === 0) return null;
  return (
    <div className="mt-2 space-y-2">
      {outputs.map((output, i) => (
        <DisplayOutputItem key={i} output={output} />
      ))}
    </div>
  );
}

const IFRAME_INJECT = `
<style>
  html, body {
    margin: 0 !important;
    padding: 0 !important;
    min-height: 0 !important;
    height: auto !important;
    max-height: none !important;
  }
  body > div, body > section, body > main, body > article, body > header, body > footer {
    min-height: 0 !important;
    height: auto !important;
  }
</style>
<script>
(function() {
  function sendHeight() {
    var body = document.body;
    if (!body) return;
    var saved = body.style.height;
    body.style.height = '0px';
    var h = body.scrollHeight;
    body.style.height = saved;
    window.parent.postMessage({ type: '__sandbox_resize', height: h }, '*');
  }
  if (typeof ResizeObserver !== 'undefined') {
    new ResizeObserver(sendHeight).observe(document.body);
  }
  window.addEventListener('load', sendHeight);
  setTimeout(sendHeight, 50);
  setTimeout(sendHeight, 300);
  sendHeight();
})();
</script>
`;

function stripAutoplay(raw: string): string {
  return raw.replace(/\s+autoplay(?:="[^"]*")?/gi, "");
}

const HtmlOutput = memo(function HtmlOutput({ html: rawHtml }: { html: string }) {
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const [height, setHeight] = useState(200);

  const html = useMemo(() => stripAutoplay(rawHtml), [rawHtml]);

  useEffect(() => {
    function handleMessage(e: MessageEvent) {
      if (
        e.data &&
        e.data.type === "__sandbox_resize" &&
        typeof e.data.height === "number"
      ) {
        if (e.source === iframeRef.current?.contentWindow) {
          setHeight(Math.min(Math.max(e.data.height + 16, 60), 2000));
        }
      }
    }
    window.addEventListener("message", handleMessage);
    return () => window.removeEventListener("message", handleMessage);
  }, []);

  const srcdoc = html.includes("</body>")
    ? html.replace("</body>", `${IFRAME_INJECT}</body>`)
    : html + IFRAME_INJECT;

  return (
    <iframe
      ref={iframeRef}
      srcDoc={srcdoc}
      sandbox="allow-scripts"
      className="w-full rounded-lg border border-zinc-800 bg-white"
      style={{ height, border: "none", minWidth: "min(40rem, 80vw)" }}
      title="HTML output"
    />
  );
});
