; LCD screenshot over SysEx (prototype), MS-70CDR 2.10 OS.
;
; Request:  F0 52 00 <model> 7C <page> F7        page 0-15
; Reply:    F0 52 00 <model> 7C <page> <128 bytes, Zoom 7-bit packed> F7
;
; The LCD RAM is two 1 KiB planes in L2 (docs/lcd-screenshot.md):
; A = 0x11816800, B = 0x11816C00, 8 rows of 128 column bytes each (bit 0 on
; top). The LCD shows A xor B (B is the blinking highlight). Page p is bytes
; 128p..128p+127 of the 2 KiB from 0x11816800: pages 0-7 are A, 8-15 are B.
; A request for page 0 first copies all 2 KiB to SNAP with interrupts off
; (about 2,000 cycles, no task switch in between); every page is then sent
; from that copy, so the 16 replies make one consistent frame.
;
; Hook: Task_MIDI's SysEx state machine jumps through a table of five states
; (0xC00EF2D0, "ldw *+b5[b4], b1 ; bnop b1" at 0xC00AED5C). State 3 handles
; the bytes after F0 52 00 <model>; sysex_bytes replaces its entry
; (stock 0xC00AED88). Entered by a branch with
;   A6 = the byte   B3 = bytes of this message seen in state 3 so far (B14+232)
; and leaves by branching to the stock state 3 (registers untouched) or to the
; end of Task_MIDI's byte loop (0xC00AEEE0), as the stock states do.
; Callee-saved registers are Task_MIDI's (A10 = ring base) and are kept.
;
; The reply copies the stock patch dump (command 0x29 -> 0x28, 0xC00AE634):
; Semaphore_pend(B14+804) (the MIDI out lock), send_byte (0xC00AE044) for the
; header, send_packed(buf, len, 0) (0xC00AE060) for the data, send_byte(F7),
; Semaphore_post. 154 bytes a reply; the USB MIDI out ring holds 1023, and a
; full ring drops bytes, so the host waits for each reply before the next
; request. Unlike 0x29, no editor mode (0x50) is needed.
;
; In DDR at BASE, RAM test region 4 (0xC07F4000-0xC07FFFFF): free on the
; 70CDR OS, and no MIDISTOMP code or buffer uses it (docs/memory.md). FLAG and
; SNAP are plain RAM there, not loaded. Every call into stock code goes through
; an absolute address (FCALL), so changing BASE only needs a reassembly.

        .equ    BASE, 0xC07F4000
        .equ    OPCODE, 0x7C
        .equ    STOCK_STATE3, 0xC00AED88
        .equ    LOOP_END, 0xC00AEEE0
        .equ    SEND_BYTE, 0xC00AE044       ; send_byte(A4)
        .equ    SEND_PACKED, 0xC00AE060     ; send_packed(A4 = buf, B4 = len, A6 = 0)
        .equ    SEM_PEND, 0xC00C7EE0
        .equ    SEM_POST, 0xC00D6B00
        .equ    TX_SEM_OFS, 804             ; B14 offsets
        .equ    MODEL_OFS, 212
        .equ    INDEX_OFS, 232
        .equ    LCD_RAM, 0x11816800         ; planes A and B, 2 KiB
        .equ    PAGES, 16
        .equ    PAGE_BYTES, 128
        .equ    FLAG, BASE + 0x7F8          ; word: 1 = this SysEx is ours
        .equ    SNAP, BASE + 0x800          ; 2 KiB copy

; Far call: B5 = target, B3 = return. Arguments must be set before.
        .macro  FCALL target
        mvkl    .S2     \target, b5
        mvkh    .S2     \target, b5
        b       .S2     b5
        addkpc  .S2     ret\@, b3, 4
ret\@:
        .endm

        .macro  SENDK value
        mvk     .S1     \value, a4
        FCALL   SEND_BYTE
        .endm

        .text
        .nocmp
        .global sysex_bytes
sysex_bytes:
        mvkl    .S1     FLAG, a1
        mvkh    .S1     FLAG, a1
        mv      .L2     b3, b1
  [b1]  b       .S1     later
        nop     5
        ; the command byte
        mvk     .S1     OPCODE, a0
        cmpeq   .L1     a6, a0, a2
        stw     .D1T1   a2, *a1
  [!a2] b       .S1     stock
        nop     5
        mvk     .L2     1, b0
        stw     .D2T2   b0, *+b14(INDEX_OFS)
        b       .S1     done
        nop     5

later:
        ldw     .D1T1   *a1, a2
        nop     4
  [!a2] b       .S1     stock
        nop     5
        cmpeq   .L2     1, b1, b2           ; the byte after the command: page
  [!b2] b       .S1     done                ; anything after it is ignored
        nop     5
        mvk     .L2     2, b0
        stw     .D2T2   b0, *+b14(INDEX_OFS)
        mv      .L1     a6, a4
        callp   .S2     reply, b3
done:
        mvkl    .S2     LOOP_END, b0
        mvkh    .S2     LOOP_END, b0
        b       .S2     b0
        nop     5

stock:
        mvkl    .S2     STOCK_STATE3, b0
        mvkh    .S2     STOCK_STATE3, b0
        b       .S2     b0
        nop     5

; reply(A4 = page)
reply:
        addk    .S2     -16, b15
        stw     .D2T2   b3, *+b15(8)
        stw     .D2T1   a10, *+b15(12)
        and     .L1     PAGES - 1, a4, a10
        mv      .L1     a10, a1
  [a1]  b       .S1     send
        nop     5

        ; page 0: copy the 2 KiB, interrupts off
        mvkl    .S1     LCD_RAM, a0
        mvkh    .S1     LCD_RAM, a0
        mvkl    .S2     SNAP, b2
        mvkh    .S2     SNAP, b2
        mvk     .S2     PAGES * PAGE_BYTES / 8, b1
        mvc     .S2     csr, b6
        and     .L2     -2, b6, b7
        mvc     .S2     b7, csr
copy:
        lddw    .D1T1   *a0++, a3:a2
        sub     .L2     b1, 1, b1
  [b1]  b       .S1     copy
        nop     3
        stdw    .D2T1   a3:a2, *b2++
        nop     1
        mvc     .S2     b6, csr

send:
        ldw     .D2T1   *+b14(TX_SEM_OFS), a4
        mvk     .L2     -1, b4
        FCALL   SEM_PEND
        SENDK   0xF0
        SENDK   0x52
        SENDK   0x00
        ldbu    .D2T1   *+b14(MODEL_OFS), a4
        FCALL   SEND_BYTE
        SENDK   OPCODE
        mv      .L1     a10, a4
        FCALL   SEND_BYTE
        mvkl    .S1     SNAP, a4
        mvkh    .S1     SNAP, a4
        shl     .S1     a10, 7, a0
        add     .L1     a0, a4, a4
        mvk     .S2     PAGE_BYTES, b4
        mvk     .L1     0, a6
        FCALL   SEND_PACKED
        SENDK   0xF7
        ldw     .D2T1   *+b14(TX_SEM_OFS), a4
        FCALL   SEM_POST

        ldw     .D2T2   *+b15(8), b3
        ldw     .D2T1   *+b15(12), a10
        addk    .S2     16, b15
        nop     3
        b       .S2     b3
        nop     5
