/**
 * Single source of truth for product branding. Every place the product name, tagline or
 * logo appears in the UI should import from here rather than hardcoding the strings, so a
 * future rename touches one file instead of a grep-and-pray across components.
 */

export const PRODUCT_NAME = "VAYUDRISHTI";
export const TAGLINE = "Engine Digital Twin";

/**
 * The full splash graphic (dark-navy card, ringed emblem, "VAYUDRISHTI" wordmark and
 * tagline stacked below it) as supplied. Its ring carries decorative text — including the
 * DRDO problem-statement code — baked into the artwork, so this file is NOT rendered
 * directly anywhere in the live UI. Kept as the canonical source asset per the branding
 * spec; anything shown on-screen uses `EMBLEM_PATH` below instead.
 */
export const LOGO_PATH = "/branding/vayudrishti-logo.png";

/**
 * The circular gold mark alone, cropped from `LOGO_PATH` well inside its inner ring so
 * none of that ring's text makes it into the crop, then masked to a transparent-cornered
 * circle. This is what the header, favicon and any on-screen watermark actually use.
 */
export const EMBLEM_PATH = "/branding/vayudrishti-emblem.png";
