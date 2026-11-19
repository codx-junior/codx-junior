/**
 * Wait for the assistant answer of one spoken message.
 *
 * The chat flow is socket based: the client posts the user message, emits
 * `codx-junior-chat` and every chunk/state arrives through `message-event`.
 * The voice loop must know when the answer is complete before speaking it, so
 * this helper watches the chats store and resolves with the finished assistant
 * message.
 *
 * It resolves with the message (even when it carries an `error`) instead of
 * hiding failures: the overlay can then speak a useful message and continue
 * the call.
 */

/**
 * Find the finished assistant reply of a user message.
 *
 * @param {Array<object>} messages
 * @param {string} userMessageId
 * @returns {object|null} The reply, or null while it does not exist/finish.
 */
export function findAssistantReply(messages, userMessageId) {
  const list = Array.isArray(messages) ? messages : []
  const key = String(userMessageId || '')
  if (!key) return null
  const userIndex = list.findIndex(message =>
    String(message?.doc_id || message?.id || '') === key
  )
  // Without the user message the store has not received this turn yet; never
  // fall back to a previous answer.
  if (userIndex < 0) return null
  const after = list.slice(userIndex + 1)
  const candidates = after.filter(message =>
    message?.role === 'assistant' &&
    !message?.meta_data?.voice_notice &&
    message?.unsent !== true
  )
  const explicit = candidates.find(message =>
    String(message.reply_to || '') === key
  )
  const reply = explicit || candidates[0]
  if (!reply) return null
  if (reply.done === false) return null
  return reply
}

/**
 * Wait until the reply of `userMessageId` is complete.
 *
 * @param {object} options
 * @param {object} options.store - `$storex` accessor.
 * @param {string} options.chatId
 * @param {string} options.userMessageId
 * @param {number} [options.timeoutMs]
 * @param {number} [options.pollMs]
 * @param {AbortSignal} [options.signal]
 * @returns {Promise<object>} finished assistant message
 */
export function waitForAssistantReply({
  store,
  chatId,
  userMessageId,
  timeoutMs = 5 * 60 * 1000,
  pollMs = 300,
  signal
} = {}) {
  const getChat = () => {
    return (
      store?.chats?.chats?.[chatId] ||
      store?.state?.chats?.chats?.[chatId] ||
      null
    )
  }

  return new Promise((resolve, reject) => {
    let settled = false
    let timer = null
    let timeoutTimer = null
    let unsubscribe = null

    const cleanup = () => {
      if (timer) clearInterval(timer)
      timer = null
      if (timeoutTimer) clearTimeout(timeoutTimer)
      timeoutTimer = null
      if (typeof unsubscribe === 'function') {
        try { unsubscribe() } catch (error) { /* store already gone */ }
      }
      unsubscribe = null
      if (signal) {
        try { signal.removeEventListener('abort', onAbort) } catch (error) { /* ignore */ }
      }
    }

    const finish = (fn, value) => {
      if (settled) return
      settled = true
      cleanup()
      fn(value)
    }

    const onAbort = () => {
      const error = new Error('voice turn aborted')
      error.name = 'AbortError'
      finish(reject, error)
    }

    const check = () => {
      const chat = getChat()
      const reply = findAssistantReply(chat?.messages, userMessageId)
      if (reply) finish(resolve, reply)
    }

    if (signal?.aborted) {
      onAbort()
      return
    }
    if (signal) signal.addEventListener('abort', onAbort, { once: true })

    // Vuex notifies on every chat mutation (including streamed chunks); the
    // interval is a fallback for stores whose subscribe is unavailable.
    try {
      if (typeof store?.store?.subscribe === 'function') {
        unsubscribe = store.store.subscribe(check)
      }
    } catch (error) {
      unsubscribe = null
    }
    timer = setInterval(check, Math.max(50, pollMs))
    check()

    timeoutTimer = setTimeout(() => {
      finish(reject, Object.assign(new Error('voice answer timed out'), {
        code: 'timeout'
      }))
    }, Math.max(1000, timeoutMs))
  })
}

export default { findAssistantReply, waitForAssistantReply }
