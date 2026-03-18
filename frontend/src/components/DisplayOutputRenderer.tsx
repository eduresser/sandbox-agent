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

const MAX_IFRAME_HEIGHT = 5000;

const IFRAME_INJECT = `
<style>
  html, body {
    margin: 0 !important;
    padding: 0 !important;
    overflow: hidden !important;
    height: auto !important;
    min-height: 0 !important;
    max-height: none !important;
  }
  * {
    scrollbar-width: thin;
    scrollbar-color: #3f3f46 transparent;
  }
  ::-webkit-scrollbar { width: 6px; height: 6px; }
  ::-webkit-scrollbar-track { background: transparent; }
  ::-webkit-scrollbar-thumb { background-color: #3f3f46; border-radius: 3px; }
</style>
<script>
(function() {
  var lastH = -1;
  var rafId = 0;
  var observer = null;

  function sendSize() {
    var body = document.body;
    if (!body) return;

    if (observer) observer.disconnect();

    var savedH = body.style.height;
    body.style.height = '0px';
    var h = body.scrollHeight;
    body.style.height = savedH;

    if (observer) observer.observe(body);

    if (h !== lastH) {
      lastH = h;
      window.parent.postMessage({ type: '__sandbox_resize', height: h }, '*');
    }
  }

  function scheduleSendSize() {
    cancelAnimationFrame(rafId);
    rafId = requestAnimationFrame(sendSize);
  }

  if (typeof ResizeObserver !== 'undefined') {
    observer = new ResizeObserver(scheduleSendSize);
    observer.observe(document.body);
  }
  window.addEventListener('load', sendSize);
  setTimeout(sendSize, 50);
  setTimeout(sendSize, 300);
  setTimeout(sendSize, 1000);
  sendSize();
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
    let growthStreak = 0;
    let prevH = 200;

    function handleMessage(e: MessageEvent) {
      if (
        e.data &&
        e.data.type === "__sandbox_resize" &&
        typeof e.data.height === "number"
      ) {
        if (e.source === iframeRef.current?.contentWindow) {
          const newH = Math.min(Math.max(e.data.height + 16, 60), MAX_IFRAME_HEIGHT);

          const delta = newH - prevH;
          if (delta > 0 && delta < 50) {
            growthStreak++;
            if (growthStreak >= 4) return;
          } else {
            growthStreak = 0;
          }
          prevH = newH;

          setHeight(prev => prev === newH ? prev : newH);
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
      className="w-full rounded-lg border border-zinc-800 bg-transparent"
      style={{ height, border: "none" }}
      title="HTML output"
    />
  );
});
