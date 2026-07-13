const BAUD_DIV = u32:868;   // 100 MHz / 115200

struct S { div: u32, frame: u10, bitpos: u4, active: u1, payload: u8 }

proc uartp {
    tx: chan<u1> out;
    config(tx: chan<u1> out) { (tx,) }
    init { zero!<S>() }
    next(s: S) {
        let txbit = if s.active { s.frame[0:1] } else { u1:1 };
        send(join(), tx, txbit);
        if s.active {
            if s.div == BAUD_DIV - u32:1 {
                let bp = s.bitpos + u4:1;
                if bp == u4:10 { S { div: u32:0, frame: s.frame, bitpos: u4:0, active: u1:0, payload: s.payload } }
                else { S { div: u32:0, frame: s.frame >> u10:1, bitpos: bp, active: u1:1, payload: s.payload } }
            } else { S { div: s.div + u32:1, frame: s.frame, bitpos: s.bitpos, active: u1:1, payload: s.payload } }
        } else {
            S { div: u32:0, frame: u1:1 ++ s.payload ++ u1:0, bitpos: u4:0, active: u1:1, payload: s.payload + u8:1 }
        }
    }
}
