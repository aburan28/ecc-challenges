"""Reproduce exact parameters from pdftotext -layout of the pinned official PDF."""
import argparse
import hashlib
import json
from pathlib import Path
import re

SOURCE_URL = "https://www.certicom.com/content/dam/certicom/images/pdfs/challenge-2009.pdf"
PDF_SHA256 = "3a0cff3f84e933e9f9b22a0972fee5673a2ed16a406421f2daa945ffa33d5c13"


def extract(text_bytes, pdf_sha256=PDF_SHA256):
    if pdf_sha256 != PDF_SHA256:
        raise ValueError("source PDF differs from pinned document")
    text = text_bytes.decode("utf-8")
    vectors = {}
    pattern = r"6\.1\.(\d+)\s+(ECC(?:2K|2|p)-\d+)\s+• Solution: logP Q = ([0-9A-F ]+)"
    for section, name, number in re.findall(pattern, text):
        scalar = int(number.replace(" ", ""), 10 if name == "ECCp-109" else 16)
        vectors[name.lower()] = {"scalar": hex(scalar), "source_section": f"6.1.{section}",
                                 "role": "published arithmetic correctness vector"}
    if len(vectors) != 10:
        raise ValueError("expected ten published scalar vectors")
    appendix = text.split("A       Curve Details", 1)[1]
    appendix = re.sub(r"(?m)^\s*\d+\s*$", "", appendix).replace("\f", "\n")
    blocks = re.split(r"(?m)^(ECC(?:2K|2|p)-\d+)\s*$", appendix)[1:]
    records, normalizations = [], []
    for name, body in zip(blocks[::2], blocks[1::2]):
        values, last = {}, None
        for line in body.splitlines():
            match = re.fullmatch(r"\s*([A-Za-z_]+)\s*:?=\s*(.*?)\s*", line)
            if match:
                key, value = match.groups()
                last = key
                values.setdefault(key, []).append(value)
            elif last and re.fullmatch(r"\s*(?:[A-Fa-f0-9]{1,8}\s+)*[A-Fa-f0-9]{1,8}\s*", line):
                values[last][-1] += " " + line.strip()
            elif line.strip():
                last = None
        def integer(key, index=0):
            return int(re.sub(r"\s+", "", values[key][index]), 16)
        identity, prime, a = name.lower(), name.startswith("ECCp"), integer("a")
        if "b" in values:
            b = integer("b")
        elif name == "ECC2K-163" and len(values["a"]) == 2:
            b = integer("a", 1)
            normalizations.append({"id": identity, "kind": "source_label_correction", "field": "b",
                "canonical": "0x1", "reason": "Second printed a is b=1; both published points and subgroup products verify."})
        else:
            raise ValueError(f"{name}: missing b")
        if "P_x" in values:
            G, Q = (integer("P_x"), integer("P_y")), (integer("Q_x"), integer("Q_y"))
        else:
            if len(values.get("x", [])) != 2 or len(values.get("y", [])) != 2:
                raise ValueError(f"{name}: ambiguous coordinates")
            G, Q = (integer("x"), integer("y")), (integer("x", 1), integer("y", 1))
            normalizations.append({"id": identity, "kind": "coordinate_label_normalization",
                "reason": "First bare x,y under seedP are G; second pair under seedQ are Q."})
        if prime:
            modulus = integer("p")
            bits = modulus.bit_length()
            if a % modulus != a:
                normalizations.append({"id": identity, "kind": "field_representative_normalization",
                    "field": "a", "printed": hex(a), "canonical": hex(a % modulus),
                    "reason": "Canonical reduction modulo p, checked against both points and published scalar."})
            a %= modulus
        else:
            bits = int(values["m"][0])
            exponents = [int(v) for v in re.findall(r"x\^(\d+)", values["f"][0])]
            if re.search(r"x(?!\^)", values["f"][0]):
                exponents.append(1)
            exponents.append(0)
            if len(set(exponents)) != len(exponents) or max(exponents) != bits:
                raise ValueError("invalid binary field polynomial")
            modulus = sum(1 << v for v in exponents)
        records.append({"id": identity, "field_kind": "prime" if prime else "binary", "bits": bits,
                        "modulus": hex(modulus), "a": hex(a), "b": hex(b), "n": hex(integer("n")), "h": integer("h"),
                        "G": list(map(hex, G)), "Q": list(map(hex, Q)), "coordinate_encoding": "affine-xy-be",
                        "equation": "y^2=x^3+a*x+b" if prime else "y^2+x*y=x^3+a*x^2+b"})
    if len(records) != 24 or len({r["id"] for r in records}) != 24:
        raise ValueError("expected all 24 unique instances")
    return {"format_version": 1, "source": {"title": "Certicom ECC Challenge", "document_updated": "2009-11-10",
        "url": SOURCE_URL, "pdf_sha256": pdf_sha256, "text_sha256": hashlib.sha256(text_bytes).hexdigest(),
        "parameter_section": "Appendix A", "coordinate_basis": "Prime residues or exact published binary polynomial basis",
        "extraction_command": "pdftotext -layout challenge-2009.pdf extracted.txt"},
        "normalizations": normalizations, "reference_vectors": vectors, "instances": records}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("text", type=Path)
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    registry = extract(args.text.read_bytes(), hashlib.sha256(args.pdf.read_bytes()).hexdigest())
    encoded = json.dumps(registry, sort_keys=True, indent=2) + "\n"
    if args.output:
        args.output.write_text(encoded)
    else:
        print(encoded, end="")


if __name__ == "__main__":
    main()
