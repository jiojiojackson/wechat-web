/* Keep mobile IME pre-edit text local; forward one committed value to Selkies. */
(() => {
    "use strict";
    const states = new WeakMap();
    const committedEvents = new WeakSet();

    function helper(event) {
        const input = event.target;
        if (!(input instanceof HTMLInputElement) || input.id !== "keyboard-input-assist") return null;
        let state = states.get(input);
        if (!state) {
            state = {composing: false, pending: null, timer: null};
            states.set(input, state);
        }
        return {input, state};
    }

    function clearTimer(state) {
        if (state.timer !== null) clearTimeout(state.timer);
        state.timer = null;
    }

    function flush(input, state) {
        clearTimer(state);
        if (state.composing || state.pending === null) return;
        const text = state.pending;
        state.pending = null;
        input.value = text;
        if (!text || !input.isConnected) return;
        const event = new InputEvent("input", {
            bubbles: true, inputType: "insertText", data: text, isComposing: false
        });
        committedEvents.add(event);
        input.dispatchEvent(event);
    }

    window.addEventListener("focus", helper, true);
    window.addEventListener("compositionstart", event => {
        const target = helper(event);
        if (!target) return;
        if (target.state.pending !== null) flush(target.input, target.state);
        clearTimer(target.state);
        target.state.composing = true;
        target.state.pending = null;
        event.stopImmediatePropagation();
    }, true);

    window.addEventListener("compositionupdate", event => {
        if (helper(event)) event.stopImmediatePropagation();
    }, true);

    window.addEventListener("compositionend", event => {
        const target = helper(event);
        if (!target) return;
        const {input, state} = target;
        clearTimer(state);
        state.composing = false;
        state.pending = typeof event.data === "string" ? event.data : input.value;
        input.value = state.pending;
        event.stopImmediatePropagation();
        // Some engines emit the final input before compositionend, others after.
        // A following native input consumes pending; otherwise flush exactly once.
        state.timer = setTimeout(() => flush(input, state), 0);
    }, true);

    window.addEventListener("input", event => {
        const target = helper(event);
        if (!target || committedEvents.has(event)) return;
        const {input, state} = target;
        if (state.composing || event.isComposing || event.inputType === "insertCompositionText") {
            event.stopImmediatePropagation();
            return;
        }
        if (state.pending !== null) {
            clearTimer(state);
            input.value = state.pending;
            state.pending = null;
            if (!input.value) event.stopImmediatePropagation();
        }
    }, true);

    for (const type of ["keydown", "keyup", "keypress"]) {
        window.addEventListener(type, event => {
            const target = helper(event);
            if (!target) return;
            if (target.state.composing || event.isComposing || event.keyCode === 229) {
                // Do not preventDefault: the phone's IME still needs the key.
                event.stopImmediatePropagation();
            }
        }, true);
    }
})();
