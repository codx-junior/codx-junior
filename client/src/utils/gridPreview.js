/**
 * Decide what a CoWorking wall tile renders.
 *
 * The wall can show one machine as a full interactive viewer and the rest as
 * cheap previews; the filmstrip and the side rail render the *same* sessions
 * again as thumbnails next to the principal stage.
 *
 * There is exactly one rule that matters for correctness: a viewport that is
 * not the live stage must never open its own copy of the live page.  For a
 * browser session the "preview" is the container's own live view (Selkies);
 * every connected client asks the container to resize the remote display to
 * its own size, so a thumbnail (`previewOnly`) and the stage attached to the
 * same session fight over the resolution.  The display then follows the
 * thumbnail while the stage still maps the pointer one-to-one to its own
 * panel, which shows up as a cursor that is in the wrong place while moving
 * (clicks still land, and a new tab - with a single client - is fine).
 *
 * `previewOnly` therefore always means "screenshot", never "open the live
 * page again".
 *
 * @param {Object} options
 * @param {boolean} options.active       Tile is the wall's selected machine.
 * @param {boolean} options.previewOnly  Tile is a thumbnail of the stage.
 * @param {boolean} options.isBrowser    Session kind is `browser`.
 * @param {boolean} options.running      Session container is running.
 * @param {string}  options.embedUrl     Same-origin live view path, if any.
 * @returns {'live'|'iframe'|'snapshot'}
 */
export function tilePreviewMode({
  active = false,
  previewOnly = false,
  isBrowser = false,
  running = false,
  embedUrl = ''
} = {}) {
  // The stage: the one interactive viewer on the screen.
  if (active && !previewOnly) return 'live'
  // Thumbnails of the stage (filmstrip / side rail): pixels only.
  if (previewOnly) return 'snapshot'
  // A browser wall tile can show its live page cheaply; it is the only
  // client for that session, so it does not compete with a stage.
  if (isBrowser && running && embedUrl) return 'iframe'
  return 'snapshot'
}
