//! `eccstark`: line-delimited JSON over stdin/stdout.
//!
//! Each input line is one request; each output line is its response.
//!
//! * `{"cmd":"prove", <params>, "start":[x,y], "steps":s, "end_is_dp":bool}`
//!   -> `{"ok":true, "end":[x,y], "proof":"<base64>"}`
//! * `{"cmd":"verify", <params>, "start":[x,y], "end":[x,y], "steps":s,
//!    "end_is_dp":bool, "proof":"<base64>"}` -> `{"ok":true}` or `{"ok":false,"error":..}`
//! * `{"cmd":"work", <params>, "coeffs":[[c,d],..], "n":"0x..", "start":[x,y],
//!    "a0":"0x..", "b0":"0x..", "max_len":L, "checkpoint_interval":k, "prove":bool}`
//!   -> `{"ok":true, "length":L, "dp":[x,y], "a":"0x..", "b":"0x..", "proof":..}`
//!   or `{"ok":true, "length":null}` when the walk is abandoned.
//!
//! `<params>` is `"dp_bits": d, "mults": [[x,y], ...]`. Numbers are 0x-hex.

use std::io::{self, BufRead, Write};

use base64::{engine::general_purpose::STANDARD as B64, Engine};
use eccstark::{prove, verify, walk_to_dp, Felt, Point, PublicInputs, WalkParams, Q};
use serde_json::{json, Value};
use winterfell::Proof;

fn u64_of(v: &Value, what: &str) -> Result<u64, String> {
    let s = v.as_str().ok_or_else(|| format!("{what} must be a 0x-hex string"))?;
    let s = s.strip_prefix("0x").ok_or_else(|| format!("{what} must start with 0x"))?;
    if s.is_empty() || s.len() > 16 {
        return Err(format!("{what} is not a 64-bit hex value"));
    }
    u64::from_str_radix(s, 16).map_err(|_| format!("{what} is not hex"))
}

fn felt(v: &Value, what: &str) -> Result<Felt, String> {
    let x = u64_of(v, what)?;
    if x >= Q {
        return Err(format!("{what} is not a canonical field element"));
    }
    Ok(Felt::new(x))
}

fn point(v: &Value, what: &str) -> Result<Point, String> {
    let a = v.as_array().filter(|a| a.len() == 2).ok_or_else(|| format!("{what} must be [x, y]"))?;
    Ok((felt(&a[0], what)?, felt(&a[1], what)?))
}

fn hex(x: Felt) -> String {
    format!("{:#x}", x.as_int())
}

fn params(req: &Value) -> Result<WalkParams, String> {
    let dp_bits = req["dp_bits"].as_u64().ok_or("dp_bits must be an integer")? as u32;
    let mults = req["mults"]
        .as_array()
        .ok_or("mults must be a list")?
        .iter()
        .enumerate()
        .map(|(i, m)| point(m, &format!("mults[{i}]")))
        .collect::<Result<Vec<_>, _>>()?;
    let p = WalkParams { dp_bits, mults };
    p.validate()?;
    Ok(p)
}

fn steps(req: &Value) -> Result<usize, String> {
    let s = req["steps"].as_u64().ok_or("steps must be an integer")?;
    if s == 0 || s > (1 << 24) {
        return Err("steps must be in [1, 2^24]".into());
    }
    Ok(s as usize)
}

fn handle(req: &Value) -> Result<Value, String> {
    match req["cmd"].as_str() {
        Some("prove") => {
            let p = params(req)?;
            let (proof, end) = prove(&p, point(&req["start"], "start")?, steps(req)?,
                                     req["end_is_dp"].as_bool().unwrap_or(false))?;
            Ok(json!({"ok": true, "end": [hex(end.0), hex(end.1)],
                      "proof": B64.encode(proof.to_bytes())}))
        }
        Some("verify") => {
            let p = params(req)?;
            let bytes = B64
                .decode(req["proof"].as_str().ok_or("proof must be base64")?)
                .map_err(|_| "proof is not valid base64")?;
            let proof = Proof::from_bytes(&bytes).map_err(|e| format!("malformed proof: {e}"))?;
            let pi = PublicInputs {
                start: point(&req["start"], "start")?,
                end: point(&req["end"], "end")?,
                steps: steps(req)?,
                end_is_dp: req["end_is_dp"].as_bool().ok_or("end_is_dp must be a bool")?,
                params: p,
            };
            verify(proof, pi)?;
            Ok(json!({"ok": true}))
        }
        Some("work") => {
            let p = params(req)?;
            let coeffs = req["coeffs"]
                .as_array()
                .ok_or("coeffs must be a list")?
                .iter()
                .map(|c| Ok((u64_of(&c[0], "coeff")?, u64_of(&c[1], "coeff")?)))
                .collect::<Result<Vec<_>, String>>()?;
            if coeffs.len() != p.r() {
                return Err("need one (c, d) per multiplier".into());
            }
            let n = u64_of(&req["n"], "n")?;
            let start = point(&req["start"], "start")?;
            let max_len = req["max_len"].as_u64().ok_or("max_len must be an integer")? as usize;
            let ck = req["checkpoint_interval"].as_u64().unwrap_or(0) as usize;
            let out = walk_to_dp(&p, &coeffs, n, start, u64_of(&req["a0"], "a0")?,
                                 u64_of(&req["b0"], "b0")?, max_len, ck);
            let Some(w) = out else {
                return Ok(json!({"ok": true, "length": null}));
            };
            let mut resp = json!({"ok": true, "length": w.length,
                                  "dp": [hex(w.end.0), hex(w.end.1)],
                                  "a": format!("{:#x}", w.a), "b": format!("{:#x}", w.b),
                                  "checkpoints": w.checkpoints.iter()
                                      .map(|c| [hex(c.0), hex(c.1)]).collect::<Vec<_>>()});
            if req["prove"].as_bool().unwrap_or(false) {
                let (proof, end) = prove(&p, start, w.length, true)?;
                debug_assert_eq!(end, w.end);
                resp["proof"] = json!(B64.encode(proof.to_bytes()));
            }
            Ok(resp)
        }
        _ => Err("unknown cmd (prove | verify | work)".into()),
    }
}

fn main() {
    std::panic::set_hook(Box::new(|_| {})); // errors are reported as JSON, not stderr noise
    let stdin = io::stdin();
    let mut out = io::stdout().lock();
    for line in stdin.lock().lines() {
        let Ok(line) = line else { break };
        if line.trim().is_empty() {
            continue;
        }
        let resp = match serde_json::from_str::<Value>(&line) {
            // A panic anywhere in decoding or verification must not kill the
            // process the server is sharing between requests.
            Ok(req) => std::panic::catch_unwind(|| handle(&req))
                .unwrap_or_else(|_| Err("internal panic while handling request".into()))
                .unwrap_or_else(|e| json!({"ok": false, "error": e})),
            Err(e) => json!({"ok": false, "error": format!("bad json: {e}")}),
        };
        let _ = writeln!(out, "{resp}");
        let _ = out.flush();
    }
}
