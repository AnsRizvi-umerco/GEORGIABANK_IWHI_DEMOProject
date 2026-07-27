#!/usr/bin/env python3
"""Simulate Pain001ToPacs008 against every sample_pain001*.xml.
Valid files -> expected_pacs008_<suffix>.xml; invalid files must fail the BRANCH."""
import importlib.util, os, re, sys
from decimal import Decimal
from xml.dom import minidom

ROOT = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("gen", os.path.join(ROOT, "gen_Pain001ToPacs008.py"))
gen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gen)  # regenerates flow.xml/node.ndf + runs base simulation
print("---")

def simulate(pain_xml):
    """Replicates the DAFS step by step. Returns (pacs008, status) or raises on BRANCH EXIT."""
    if not re.search(r'pain\.001\.001\.09', pain_xml):  # step 0: BRANCH validation
        raise ValueError("Input is not a pain.001.001.09 document - translation aborted")
    pipe = {}
    for _, out, rx in gen.HDR_EXTRACTS:
        pipe[out] = re.sub(rx, r"\1", pain_xml)
    blocks = [b for b in pipe["txnRegion"].split("<CdtTrfTxInf>") if b.strip()]
    def subst(t, p):
        for k in sorted(p, key=len, reverse=True):
            t = t.replace(f"%{k}%", p[k])
        return t
    pipe["pacs008"] = subst(gen.HEADER_TEMPLATE, pipe)
    for blk in blocks:
        for _, out, rx in gen.TXN_EXTRACTS:
            pipe[out] = re.sub(rx, r"\1", blk)
        pipe["pacs008"] = subst(gen.TXN_TEMPLATE, pipe)
    return subst(gen.TRAILER_TEMPLATE, pipe), "TRANSLATED_PACS008"

def check(pacs):
    minidom.parseString(pacs)
    ns = "urn:iso:std:iso:20022:tech:xsd:pacs.008.001.08"
    assert ns in pacs and "%" not in pacs
    amts = [Decimal(m) for m in re.findall(r'<IntrBkSttlmAmt Ccy="USD">([\d.]+)</IntrBkSttlmAmt>', pacs)]
    ttl = Decimal(re.search(r'<TtlIntrBkSttlmAmt Ccy="USD">([\d.]+)<', pacs).group(1))
    n = int(re.search(r'<NbOfTxs>(\d+)<', pacs).group(1))
    assert len(amts) == n and sum(amts) == ttl, f"txns={len(amts)} NbOfTxs={n} sum={sum(amts)} ttl={ttl}"
    return n, ttl

failures = 0
for fn in sorted(os.listdir(ROOT)):
    if not (fn.startswith("sample_pain001") and fn.endswith(".xml")):
        continue
    src = open(os.path.join(ROOT, fn)).read()
    expect_invalid = "invalid" in fn
    try:
        pacs, status = simulate(src)
        if expect_invalid:
            print(f"FAIL  {fn}: should have been REJECTED but translated!"); failures += 1
            continue
        n, ttl = check(pacs)
        suffix = fn.replace("sample_pain001", "").replace(".xml", "").strip("_") or "payroll5"
        out = os.path.join(ROOT, f"expected_pacs008_{suffix}.xml" if suffix != "payroll5" else "expected_pacs008.xml")
        with open(out, "w") as f:
            f.write(pacs + "\n")
        print(f"PASS  {fn}: {n} txn(s), total {ttl} USD -> {os.path.basename(out)}")
    except ValueError as e:
        if expect_invalid:
            print(f"PASS  {fn}: correctly REJECTED -> '{e}'")
        else:
            print(f"FAIL  {fn}: unexpected rejection: {e}"); failures += 1
sys.exit(1 if failures else 0)
