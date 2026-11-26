/**
 * Attachments for composers that are NOT a full chat (`ChatInputBox` mounted
 * directly, e.g. harness mode).
 *
 * `ChatInputBox` only emits `paste` and `upload-files`: turning a File into a
 * `ChatAttachment` (and the clipboard image into a File) is the host's job.
 * `Chat.vue` does it inline; the harness composers used to ignore both events,
 * so a pasted screenshot or an uploaded file silently disappeared. These
 * helpers keep ONE implementation of that conversion for every such host.
 */
import ChatAttachment from '@/api/model/ChatAttachment.js'

const MAX_ATTACHMENT_SIZE_MB = 50
const MAX_ATTACHMENT_SIZE_BYTES = MAX_ATTACHMENT_SIZE_MB * 1024 * 1024

function notifyError(notify, text) {
  if (typeof notify === 'function') notify(text)
  else console.error(text)
}

/**
 * Normalise an attachment to the JSON shape the API expects AND to the fields
 * a preview needs (`name`, `type`, `data` as a data URL).
 *
 * @param {object|ChatAttachment} attachment Attachment instance or plain JSON.
 * @return {{name: string, type: string, data: string}} Payload for the API.
 */
export function attachmentPayload(attachment) {
  const json = (attachment && attachment.toJSON && attachment.toJSON()) || attachment || {}
  // The API model (``ChatAttachment``) is ``file_name/file_type/base64_data``;
  // the composer previews and the harness payload read the short aliases.
  return {
    ...json,
    file_name: json.file_name || json.name || 'attachment',
    file_type: json.file_type || json.type || 'application/octet-stream',
    file_size: Number(json.file_size || json.size || 0),
    base64_data: json.base64_data || json.data || json.content || '',
    name: json.file_name || json.name || 'attachment',
    type: json.file_type || json.type || 'application/octet-stream',
    data: json.base64_data || json.data || json.content || ''
  }
}

/**
 * Payload of every pending attachment, ready to be posted with the message.
 *
 * @param {Array} attachments Pending attachments.
 * @return {Array<object>} API payloads.
 */
export function attachmentPayloads(attachments) {
  return (attachments || []).filter(Boolean).map(attachmentPayload)
}

/**
 * Convert one File into a ChatAttachment.
 *
 * @param {File} file File chosen or pasted by the user.
 * @param {Function} [notify] Error notifier (UI toast).
 * @return {Promise<ChatAttachment|null>} The attachment, or null when rejected.
 */
export async function attachmentFromFile(file, notify) {
  if (!file) return null
  if (file.size > MAX_ATTACHMENT_SIZE_BYTES) {
    notifyError(notify, `File exceeds maximum size of ${MAX_ATTACHMENT_SIZE_MB}MB`)
    return null
  }
  try {
    return await ChatAttachment.fromFile(file)
  } catch (error) {
    console.error('Error preparing attachment:', error)
    notifyError(notify, 'Failed to process file')
    return null
  }
}

/**
 * Convert a FileList/array (composer `upload-files` event) into attachments.
 *
 * @param {FileList|Array<File>} files Files to convert.
 * @param {Function} [notify] Error notifier (UI toast).
 * @return {Promise<Array<ChatAttachment>>} Valid attachments (invalid skipped).
 */
export async function attachmentsFromFiles(files, notify) {
  const result = []
  for (const file of Array.from(files || [])) {
    const attachment = await attachmentFromFile(file, notify)
    if (attachment) result.push(attachment)
  }
  return result
}

/**
 * Extract the image of a paste event as an attachment.
 *
 * Covers both the plain `clipboardData.files` (screenshot tools) and the
 * `clipboardData.items` list (browsers copy images as items with `kind=file`).
 *
 * @param {ClipboardEvent} event The composer `paste` event.
 * @param {Function} [notify] Error notifier (UI toast).
 * @return {Promise<ChatAttachment|null>} The pasted image, or null when the
 *   clipboard carries no image (the text paste must keep its default).
 */
export async function attachmentFromPaste(event, notify) {
  const clipboard = event && event.clipboardData
  if (!clipboard) return null
  const candidates = []
  for (const item of Array.from(clipboard.items || [])) {
    if (item.kind === 'file' && String(item.type || '').startsWith('image/')) {
      const file = item.getAsFile()
      if (file) candidates.push(file)
    }
  }
  if (!candidates.length) {
    for (const file of Array.from(clipboard.files || [])) {
      if (String(file.type || '').startsWith('image/')) candidates.push(file)
    }
  }
  if (!candidates.length) return null
  return attachmentFromFile(candidates[0], notify)
}
