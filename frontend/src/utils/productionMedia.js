import { productionArtifactUrl, productionUploadUrl } from '../api'
import { withRevision } from './format'

export function mediaUrl(file, productionId) {
  if (!file) return ''
  if (file.previewUrl || file.preview_url) return file.previewUrl || file.preview_url
  if (file.url) return file.url
  if (file.kind === 'production_video' && file.id) return productionUploadUrl(file.id)
  if (productionId && file.path && !String(file.path).startsWith('/')) {
    const name = String(file.path).split(/[\\/]/).pop()
    return name ? productionArtifactUrl(productionId, name) : ''
  }
  if (typeof file.path === 'string' && file.path.startsWith('/api/')) return file.path
  return ''
}

export function clipMediaUrl(clip, productionId) {
  if (!clip) return ''
  if (clip.clip_url || clip.preview_url || clip.previewUrl || clip.url) {
    return clip.clip_url || clip.preview_url || clip.previewUrl || clip.url
  }
  const path = clip.clip_path || clip.path
  if (!productionId || !path) return ''
  const name = String(path).split(/[\\/]/).pop()
  return name ? productionArtifactUrl(productionId, name) : ''
}

export function frameMediaUrl(productionId, name) {
  if (!productionId || !name) return ''
  return productionArtifactUrl(productionId, name)
}

export function outputVideoUrl(payload) {
  const id = payload?.production_id
  const url =
    payload?.video_url ||
    payload?.output?.video_url ||
    (id ? productionArtifactUrl(id, 'final.mp4') : '')
  return withRevision(url, payload?.updated_at || payload?.output?.updated_at)
}

export function sourceVideoUrl(payload) {
  const source = payload?.source_video || payload?.reference_video || {}
  if (source.url) return source.url
  if (source.upload_id) return productionUploadUrl(source.upload_id)
  if (payload?.production_id) {
    const name = source.filename || source.path?.split(/[\\/]/).pop() || 'source.mp4'
    return productionArtifactUrl(payload.production_id, name)
  }
  return mediaUrl(source)
}
