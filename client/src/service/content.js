import Service from "./service"

// Read a File as a data url: uploads travel base64 encoded inside a json body,
// so no multipart dependency is needed server side.
function readAsDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result || ""))
    reader.onerror = () => reject(reader.error || new Error("Cannot read the file"))
    reader.readAsDataURL(file)
  })
}

const enc = (value) => encodeURIComponent(value || "")

export class ContentService extends Service {
  // Output formats, profiles involved and upload limits.
  formats() {
    return this.$api.get('/api/content/formats')
  }

  items() {
    return this.$api.get('/api/content/items')
  }

  item(contentId) {
    return this.$api.get(`/api/content/items/${enc(contentId)}`)
  }

  create(payload) {
    return this.$api.post('/api/content/items', payload)
  }

  // The API uses POST for partial updates too, so one verb covers both.
  update(contentId, payload) {
    return this.$api.post(`/api/content/items/${enc(contentId)}`, payload)
  }

  remove(contentId) {
    return this.$api.del(`/api/content/items/${enc(contentId)}`)
  }

  addSource(contentId, source) {
    return this.$api.post(`/api/content/items/${enc(contentId)}/sources`, source)
  }

  removeSource(contentId, sourceId) {
    return this.$api.del(
      `/api/content/items/${enc(contentId)}/sources/${enc(sourceId)}`
    )
  }

  // Uploads one file into <content>/sources/ and returns the updated item.
  async upload(contentId, file) {
    const data_base64 = await readAsDataUrl(file)
    return this.$api.post(`/api/content/items/${enc(contentId)}/uploads`, {
      name: file.name,
      mime: file.type,
      data_base64
    })
  }

  // Enqueues the media jobs, writes build.md and returns the builder task.
  build(contentId, payload) {
    return this.$api.post(`/api/content/items/${enc(contentId)}/build`, payload || {})
  }

  fileUrl(contentId, name) {
    const path = String(name || "")
      .split('/')
      .map(encodeURIComponent)
      .join('/')
    return `/api/content/items/${enc(contentId)}/file/${path}`
  }
}
