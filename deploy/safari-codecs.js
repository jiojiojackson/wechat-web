/* The small JPEG ImageDecoder subset used by Selkies, for browsers without it. */
(() => {
    "use strict";
    if (typeof window.ImageDecoder === "function") return;

    async function decodeJpeg(blob) {
        if (typeof window.createImageBitmap === "function") {
            try { return await window.createImageBitmap(blob); }
            catch (_) { /* Older Safari can expose a partial ImageBitmap API. */ }
        }
        const image = new Image();
        const url = URL.createObjectURL(blob);
        try {
            await new Promise((resolve, reject) => {
                image.onload = resolve;
                image.onerror = () => reject(new DOMException("JPEG could not be decoded", "EncodingError"));
                image.src = url;
            });
        } finally {
            image.onload = image.onerror = null;
            URL.revokeObjectURL(url);
        }
        // Selkies releases every drawn image with close(), as for VideoFrame.
        image.close = () => { image.src = ""; };
        return image;
    }

    class SelkiesJpegDecoder {
        constructor(options) {
            if (options?.type !== "image/jpeg") throw new DOMException("Only JPEG is supported", "NotSupportedError");
            this._blob = new Blob([options.data], {type: "image/jpeg"});
            this._closed = false;
        }
        async decode() {
            if (this._closed) throw new DOMException("Decoder is closed", "InvalidStateError");
            const image = await decodeJpeg(this._blob);
            if (this._closed) {
                image.close();
                throw new DOMException("Decoder was closed", "AbortError");
            }
            return {image, complete: true};
        }
        close() {
            // A decoded image belongs to the caller until it is drawn and closed.
            this._closed = true;
            this._blob = null;
        }
        static async isTypeSupported(type) { return type === "image/jpeg"; }
    }
    window.ImageDecoder = SelkiesJpegDecoder;
})();
