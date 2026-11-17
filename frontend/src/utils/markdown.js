import DOMPurify from 'dompurify'
import { marked } from 'marked'

marked.setOptions({
  gfm: true,
  breaks: true,
})

export function renderMarkdown(text) {
  if (!text) return ''
  return DOMPurify.sanitize(marked.parse(String(text)), {
    USE_PROFILES: { html: true },
    ADD_ATTR: ['target', 'rel'],
  })
}

