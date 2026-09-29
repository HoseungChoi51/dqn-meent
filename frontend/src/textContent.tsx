import Markdown from 'react-markdown';
import type { Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';

const components: Components = {
  a: ({ href, children }) => href ? <a href={href} target={/^https?:\/\//.test(href) ? '_blank' : undefined} rel="noreferrer">{children}</a> : <span>{children}</span>,
  table: ({ children }) => <div className="markdown-table"><table>{children}</table></div>,
  // Messages support text markup without loading remote images from model output.
  img: ({ alt }) => <span>{alt}</span>,
};

export function TextContent({ text }: { text: unknown }) {
  if (text != null && typeof text !== 'string') {
    return <div className="text-content markdown-content"><pre><code>{JSON.stringify(text, null, 2)}</code></pre></div>;
  }
  return <div className="text-content markdown-content">
    <Markdown remarkPlugins={[remarkGfm]} components={components} skipHtml>{text || ''}</Markdown>
  </div>;
}
