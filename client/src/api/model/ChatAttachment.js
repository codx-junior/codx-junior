/**
 * Chat Attachment Model
 * 
 * Represents a file attached to a chat or message with file metadata and base64 data.
 * Corresponds to the Python ChatAttachment model.
 */

// Raster formats the vision APIs we talk to (OpenAI compatible providers and
// Anthropic through LiteLLM) can decode. Anything else is re-encoded before the
// attachment is stored: the API rejects unknown types and providers replace an
// undecodable image with an "[Unsupported Image]" placeholder, which looks like
// "the AI cannot see my screenshot".
const VISION_IMAGE_TYPES = ['image/png', 'image/jpeg', 'image/webp', 'image/gif']

// Extensions used to recognise an image when the browser reports NO media type
// (mobile shares often send `file.type === ''`).
const IMAGE_EXTENSIONS = [
  'png', 'jpg', 'jpeg', 'webp', 'gif', 'bmp', 'tif', 'tiff', 'svg', 'heic', 'heif', 'avif'
]

// Longest side kept when re-encoding. Phone screenshots/photos are ~4000px wide;
// sending them at that size only makes the request heavier.
const MAX_IMAGE_SIDE = 2048

const JPEG_QUALITY = 0.92

function normalizedType(file) {
  return String(file?.type || '').split(';')[0].trim().toLowerCase()
}

function fileExtension(file) {
  const name = String(file?.name || '')
  return name.includes('.') ? name.split('.').pop().toLowerCase() : ''
}

function isVisionImageType(type) {
  return VISION_IMAGE_TYPES.includes(String(type || '').split(';')[0].trim().toLowerCase())
}

function looksLikeImage(file) {
  if (normalizedType(file).startsWith('image/')) return true
  return normalizedType(file) === '' && IMAGE_EXTENSIONS.includes(fileExtension(file))
}

/**
 * Decode a File into a drawable source.
 *
 * `createImageBitmap` is used first (fast, no DOM); the `<img>` fallback covers
 * browsers that cannot decode the format through the bitmap API.
 *
 * @param {File} file Image file.
 * @return {Promise<{source, width, height, close: Function}>}
 */
async function decodeImage(file) {
  if (typeof createImageBitmap === 'function') {
    try {
      const bitmap = await createImageBitmap(file)
      return {
        source: bitmap,
        width: bitmap.width,
        height: bitmap.height,
        close: () => bitmap.close?.()
      }
    } catch (error) {
      // Fall through to the <img> decoder.
    }
  }
  const url = URL.createObjectURL(file)
  try {
    const image = await new Promise((resolve, reject) => {
      const img = new Image()
      img.onload = () => resolve(img)
      img.onerror = () => reject(new Error('the browser cannot decode this image'))
      img.src = url
    })
    return {
      source: image,
      width: image.naturalWidth,
      height: image.naturalHeight,
      close: () => URL.revokeObjectURL(url)
    }
  } catch (error) {
    URL.revokeObjectURL(url)
    throw error
  }
}

function canvasToBlob(canvas, mime) {
  return new Promise((resolve, reject) => {
    canvas.toBlob(
      blob => (blob ? resolve(blob) : reject(new Error('image encoding failed'))),
      mime,
      JPEG_QUALITY
    )
  })
}

/**
 * Re-encode an image file to a format the LLM vision APIs accept (PNG or JPEG).
 *
 * `file.type` is whatever the device reported: an iPhone gallery screenshot
 * arrives as `image/heic` and a picture shared from another app can arrive with
 * no type at all. Both used to be stored verbatim, so the API rejected the
 * attachment ("type not allowed") or the model could not decode it. Editing the
 * picture in the chat worked because the editor re-encodes it through a canvas —
 * which is exactly what happens here, up front, for every picture.
 *
 * Best effort: when the browser cannot decode the image the original file is
 * returned unchanged and the API sniffs/converts it server side.
 *
 * @param {File} file Original file.
 * @return {Promise<File>} A provider-safe file (PNG/JPEG), or the original one.
 */
export async function normalizeImageFile(file) {
  if (!file || !looksLikeImage(file)) return file
  if (isVisionImageType(file.type)) return file

  let decoded
  try {
    decoded = await decodeImage(file)
  } catch (error) {
    console.warn('[ChatAttachment] Could not decode image, keeping the original:', file.name, error)
    return file
  }

  try {
    const scale = Math.min(1, MAX_IMAGE_SIDE / Math.max(decoded.width, decoded.height || 1))
    const width = Math.max(1, Math.round(decoded.width * scale))
    const height = Math.max(1, Math.round(decoded.height * scale))

    // PNG sources and untyped files keep PNG (screenshots are text: lossless
    // keeps them readable); declared photos are re-encoded as JPEG.
    const declared = normalizedType(file)
    const mime = (declared === 'image/png' || declared === '') ? 'image/png' : 'image/jpeg'

    const canvas = document.createElement('canvas')
    canvas.width = width
    canvas.height = height
    const ctx = canvas.getContext('2d')
    if (mime === 'image/jpeg') {
      // JPEG has no alpha channel: transparent areas would turn black.
      ctx.fillStyle = '#ffffff'
      ctx.fillRect(0, 0, width, height)
    }
    ctx.imageSmoothingEnabled = true
    ctx.imageSmoothingQuality = 'high'
    ctx.drawImage(decoded.source, 0, 0, width, height)

    const blob = await canvasToBlob(canvas, mime)
    const baseName = (file.name || 'image').replace(/\.[^.]+$/, '')
    const extension = mime === 'image/png' ? 'png' : 'jpg'
    return new File([blob], `${baseName}.${extension}`, {
      type: mime,
      lastModified: Date.now()
    })
  } catch (error) {
    console.warn('[ChatAttachment] Image conversion failed, keeping the original:', file.name, error)
    return file
  } finally {
    decoded.close?.()
  }
}

export class ChatAttachment {
  constructor(options = {}) {
    this.file_name = options.file_name || ''
    this.file_type = options.file_type || 'application/octet-stream'
    this.file_size = options.file_size || 0
    this.base64_data = options.base64_data || ''
    this.uploaded_at = options.uploaded_at || new Date().toISOString()
  }

  isValid() {
    return this.file_name && this.file_type && this.base64_data
  }

  getValidationErrors() {
    const errors = []
    if (!this.file_name || this.file_name.trim().length === 0) {
      errors.push('File name is required')
    }
    if (!this.file_type || this.file_type.trim().length === 0) {
      errors.push('File type is required')
    }
    if (!this.base64_data || this.base64_data.trim().length === 0) {
      errors.push('File data is required')
    }
    if (this.file_size <= 0) {
      errors.push('File size must be greater than 0')
    }
    return errors
  }

  getExtension() {
    const parts = this.file_name.split('.')
    return parts.length > 1 ? parts[parts.length - 1].toLowerCase() : ''
  }

  isImage() {
    return this.file_type.startsWith('image/')
  }

  // Audio notes / voice messages (recorded with MediaRecorder or uploaded).
  isAudio() {
    return this.file_type.startsWith('audio/')
  }

  isDocument() {
    const docTypes = ['application/pdf', 'application/msword', 'application/vnd.openxmlformats-officedocument']
    return docTypes.some(type => this.file_type.includes(type))
  }

  getFormattedSize() {
    const units = ['B', 'KB', 'MB', 'GB']
    let size = this.file_size
    let unitIndex = 0
    
    while (size >= 1024 && unitIndex < units.length - 1) {
      size /= 1024
      unitIndex++
    }
    
    return `${size.toFixed(2)} ${units[unitIndex]}`
  }

  static formatSize(bytes) {
    const units = ['B', 'KB', 'MB', 'GB']
    let size = bytes
    let unitIndex = 0
    
    while (size >= 1024 && unitIndex < units.length - 1) {
      size /= 1024
      unitIndex++
    }
    
    return `${size.toFixed(2)} ${units[unitIndex]}`
  }

  toJSON() {
    return {
      file_name: this.file_name,
      file_type: this.file_type,
      file_size: this.file_size,
      base64_data: this.base64_data,
      uploaded_at: this.uploaded_at
    }
  }

  static fromJSON(json) {
    return new ChatAttachment(json)
  }

  /**
   * Build an attachment from a File.
   *
   * Images are normalized first (see `normalizeImageFile`): every picture ends
   * up as PNG/JPEG, which is both an allowed attachment type for the API and a
   * format the vision models can decode. Other files (audio notes, documents)
   * are stored untouched.
   *
   * @param {File} rawFile File chosen/dropped/pasted by the user.
   * @return {Promise<ChatAttachment>}
   */
  static async fromFile(rawFile) {
    const file = await normalizeImageFile(rawFile)
    return new Promise((resolve, reject) => {
      const reader = new FileReader()
      reader.onload = (e) => {
        const base64Data = e.target.result.split(',')[1]
        resolve(new ChatAttachment({
          file_name: file.name,
          file_type: file.type || 'application/octet-stream',
          file_size: file.size,
          base64_data: base64Data,
          uploaded_at: new Date().toISOString()
        }))
      }
      reader.onerror = (err) => reject(err)
      reader.readAsDataURL(file)
    })
  }

  clone() {
    return new ChatAttachment(this.toJSON())
  }
}

export default ChatAttachment
