#!/usr/bin/env python3
"""
Generator for the Edi820ToExcel Deploy Anywhere Flow Service (DAFS).

    sig_in : edi820 (String) -- raw X12 820 (payment order / remittance advice)
    sig_out: csv    (String) -- Excel-openable CSV: payment summary block +
                                one row per RMR invoice + TOTAL row
             status (String) -- CONVERTED_820_EXCEL

Flow: 2x BRANCH validate (ST*820, BPR) -> 8x regex header extract -> strip to
      first RMR -> tokenize "RMR*" -> sizeOfList -> TRANSFORM summary block ->
      LOOP invoices (5x replace + append row) -> TOTAL row -> debugLog

DAFS-safe: pub.string:replace capture groups only (no %array[N]%), whole-array
tokenize/sizeOfList/LOOP, no pub.xml, no getLastError. Handbook Parts III-V.
"""
import os, re, time
from xml.dom import minidom

ROOT = os.path.dirname(os.path.abspath(__file__))
SVC = "Edi820ToExcel"
SVC_DIR = os.path.join(ROOT, "ns", "project", "georgiabank_iwhi_demo", "integrations", SVC)
USER = "ans.rizvi@umerco.com"
NOW_MS = int(time.time() * 1000)

def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

# (label, source_field, regex, out_field) -- header extracts off edi820
HDR_EXTRACTS = [
    ("ExtractPaymentAmt", "edi820",     r'(?s).*?BPR\*[^*~]*\*([^*~]*).*',                          "bprAmount"),
    ("ExtractPayMethod",  "edi820",     r'(?s).*?BPR\*[^*~]*\*[^*~]*\*[^*~]*\*([^*~]*).*',          "payMethod"),
    ("ExtractTraceNum",   "edi820",     r'(?s).*?TRN\*[^*~]*\*([^*~]*).*',                          "traceNum"),
    ("ExtractPayDateRaw", "edi820",     r'(?s).*?DTM\*097\*([^*~]*).*',                             "payDateRaw"),
    ("FormatPayDate",     "payDateRaw", r'(?s)^(\d{4})(\d{2})(\d{2})$',                             "payDateFmt"),
    ("ExtractPayerName",  "edi820",     r'(?s).*?N1\*PR\*([^*~]*).*',                               "payerName"),
    ("ExtractPayeeName",  "edi820",     r'(?s).*?N1\*PE\*([^*~]*).*',                               "payeeName"),
    ("ExtractRmrRegion",  "edi820",     r'(?s)^.*?RMR\*(.*)$',                                      "rmrRegion"),
]
# per-invoice extracts off the current RMR block (scalar valueList)
TXN_EXTRACTS = [
    ("InvNumber",   "valueList",  r'(?s)^[^*~]*\*([^*~]*).*',                                  "invoiceNum"),
    ("InvPaid",     "valueList",  r'(?s)^[^*~]*\*[^*~]*\*[^*~]*\*([^*~]*).*',                  "invPaid"),
    ("InvGross",    "valueList",  r'(?s)^[^*~]*\*[^*~]*\*[^*~]*\*[^*~]*\*([^*~]*).*',          "invGross"),
    ("InvDateRaw",  "valueList",  r'(?s).*?DTM\*003\*([^*~]*).*',                              "invDateRaw"),
    ("FormatInvDate","invDateRaw", r'(?s)^(\d{4})(\d{2})(\d{2})$',                             "invDateFmt"),
]
DATE_REPL = "$1-$2-$3"  # used by the two Format* steps instead of $1

CSV_HEADER_TEMPLATE = (
    'EDI 820 Remittance Advice\n'
    'Payer,%payerName%\n'
    'Payee,%payeeName%\n'
    'Payment Method,%payMethod%\n'
    'Payment Date,%payDateFmt%\n'
    'Trace Number,%traceNum%\n'
    'Payment Amount,%bprAmount%\n'
    'Invoice Count,%invoiceCount%\n'
    '\n'
    'Invoice Number,Invoice Date,Amount Paid,Invoice Amount'
)
CSV_ROW_TEMPLATE = '%csv%\n%invoiceNum%,%invDateFmt%,%invPaid%,%invGross%'
CSV_TRAILER_TEMPLATE = '%csv%\nTOTAL,,%bprAmount%,'
DEBUG_MSG = 'EDI 820 %traceNum% converted: %invoiceCount% invoices, payment total %bprAmount% USD'
VALIDATIONS = [  # (regex label, failure message)
    (r'edi820 = /ST\*820/', 'Not an X12 820 (ST*820 segment not found) - conversion aborted'),
    (r'edi820 = /BPR\*/',   'X12 820 missing BPR payment segment - conversion aborted'),
]

# ---------------------------------------------------------------- ndf helpers
def field_decl(name, ftype="string", dim=0, node_type="unknown", hints=False,
               rec_children=None, larger=False):
    parts = ['<record javaclass="com.wm.util.Values">',
             f'  <value name="node_type">{node_type}</value>',
             '  <value name="node_subtype">unknown</value>']
    if hints:
        parts += ['  <value name="node_comment"></value>',
                  '  <record name="node_hints" javaclass="com.wm.util.Values">',
                  f'    <value name="field_largerEditor">{"true" if larger else "false"}</value>',
                  '    <value name="field_password">false</value>',
                  '    <value name="field_usereditable">false</value>',
                  '  </record>']
    parts += ['  <value name="is_public">false</value>',
              f'  <value name="field_name">{name}</value>',
              f'  <value name="field_type">{ftype}</value>',
              f'  <value name="field_dim">{dim}</value>']
    if dim == 1:
        parts.append('  <value name="is_soap_array_encoding_used">false</value>')
    parts += ['  <value name="nillable">true</value>',
              '  <value name="form_qualified">false</value>',
              '  <value name="is_global">false</value>']
    if node_type == "record":
        kids = "\n".join(rec_children or [])
        parts.append(f'  <array name="rec_fields" type="record" depth="1">\n{kids}\n  </array>')
    parts.append('</record>')
    return "\n".join(parts)

def wrap_fields(fields, group_name=None):
    fn = f'    <value name="field_name">{group_name}</value>\n' if group_name else ''
    kids = "\n".join(fields)
    return ('<Values version="2.0">\n'
            '  <record name="xml" javaclass="com.wm.util.Values">\n'
            '    <value name="node_type">record</value>\n'
            '    <value name="node_subtype">unknown</value>\n'
            '    <value name="is_public">false</value>\n'
            + fn +
            '    <value name="field_type">record</value>\n'
            '    <value name="field_dim">0</value>\n'
            f'    <array name="rec_fields" type="record" depth="1">\n{kids}\n    </array>\n'
            '  </record>\n'
            '</Values>')

def svc_field(name, ftype="string", larger=False):
    return field_decl(name, ftype, node_type="field", hints=True, larger=larger)

def pipe_field(name, ftype="string", dim=0):
    return field_decl(name, ftype, dim=dim, node_type="unknown")

def mapset(field, value, variables):
    v = "true" if variables else "false"
    return (f'<MAPSET NAME="Setter" OVERWRITE="true" VARIABLES="{v}" GLOBALVARIABLES="false" FIELD="/{field};1;0">\n'
            '  <DATA ENCODING="XMLValues" I18N="true">\n'
            '<Values version="2.0">\n'
            f'  <value name="xml">{esc(value)}</value>\n'
            '</Values>\n'
            '</DATA>\n'
            '</MAPSET>')

def mapcopy(frm, to, ftypes=(1, 1)):
    return f'<MAPCOPY FROM="/{frm};{ftypes[0]};0" TO="/{to};{ftypes[1]};0"/>'

def invoke(label, service, input_body, maptarget, mapsource, output_body=""):
    return (f'<INVOKE NAME="{label}" SERVICE="{service}">\n'
            '  <COMMENT></COMMENT>\n'
            '  <!-- nodes -->\n'
            '<MAP MODE="INPUT">\n'
            '  <COMMENT></COMMENT>\n'
            f'  <MAPTARGET>\n{maptarget}\n  </MAPTARGET>\n'
            f'  <MAPSOURCE>\n{mapsource}\n  </MAPSOURCE>\n'
            '  <!-- nodes -->\n'
            f'{input_body}\n'
            '</MAP>\n'
            '<MAP MODE="OUTPUT">\n'
            '  <COMMENT></COMMENT>\n'
            f'{output_body}\n'
            '</MAP>\n'
            '</INVOKE>')

def transform(sets, maptarget, mapsource):
    return ('<MAP NAME="TRANSFORM" TIMEOUT="" MODE="STANDALONE">\n'
            '  <COMMENT></COMMENT>\n'
            f'  <MAPTARGET>\n{maptarget}\n  </MAPTARGET>\n'
            f'  <MAPSOURCE>\n{mapsource}\n  </MAPSOURCE>\n'
            '  <!-- nodes -->\n'
            + "\n".join(sets) + '\n'
            '</MAP>')

def replace_invoke(label, src_field, regex, out_field, repl="$1"):
    mt = wrap_fields([svc_field("inString"), svc_field("searchString"),
                      svc_field("replaceString"), svc_field("useRegex")], "replaceInput")
    ms = wrap_fields([pipe_field(src_field)])
    inp = "\n".join([
        mapcopy(src_field, "inString"),
        mapset("searchString", regex, variables=False),
        mapset("replaceString", repl, variables=False),
        mapset("useRegex", "true", variables=False),
    ])
    return invoke(label, "pub.string:replace", inp, mt, ms, mapcopy("value", out_field))

def branch_step(label_expr, fail_msg):
    return ('<BRANCH LABELEXPRESSIONS="true">\n'
            '  <COMMENT></COMMENT>\n'
            '  <!-- nodes -->\n'
            f'<SEQUENCE NAME="{label_expr}" EXIT-ON="FAILURE">\n'
            '  <COMMENT></COMMENT>\n'
            '</SEQUENCE>\n'
            '<SEQUENCE NAME="$default" EXIT-ON="FAILURE">\n'
            '  <COMMENT></COMMENT>\n'
            f'<EXIT NAME="EXIT" FROM="$parent" SIGNAL="FAILURE" FAILURE-MESSAGE="{fail_msg}">\n'
            '  <COMMENT></COMMENT>\n'
            '</EXIT>\n'
            '</SEQUENCE>\n'
            '</BRANCH>')

# ---------------------------------------------------------------- step list
steps = []

for label_expr, fail in VALIDATIONS:
    steps.append((branch_step(label_expr, fail), "branch", None, None))

for label, src, rx, out in HDR_EXTRACTS:
    repl = DATE_REPL if label.startswith("Format") else "$1"
    steps.append((replace_invoke(label, src, rx, out, repl),
                  "invoke", ("replace", "pub.string:replace", "String", "string"), None))

# tokenize rmrRegion by "RMR*"
mt = wrap_fields([svc_field("inString"), svc_field("delim"), svc_field("useDelimsAsSet")], "tokenizeInput")
ms = wrap_fields([pipe_field("rmrRegion")])
inp = "\n".join([mapcopy("rmrRegion", "inString"),
                 mapset("delim", "RMR*", variables=False),
                 mapset("useDelimsAsSet", "false", variables=False)])
steps.append((invoke("SplitInvoices", "pub.string:tokenize", inp, mt, ms),
              "invoke", ("tokenize", "pub.string:tokenize", "String", "string"), None))

# sizeOfList -> invoiceCount
mt = wrap_fields([field_decl("fromList", "object", dim=1, node_type="field", hints=True)], "sizeOfListInput")
ms = wrap_fields([pipe_field("valueList", dim=1)])
steps.append((invoke("CountInvoices", "pub.list:sizeOfList",
                     mapcopy("valueList", "fromList"), mt, ms,
                     mapcopy("size", "invoiceCount")),
              "invoke", ("sizeOfList", "pub.list:sizeOfList", "List", "list"), None))

# TRANSFORM: summary block
mt = wrap_fields([pipe_field("csv")])
ms = wrap_fields([pipe_field(f) for f in
                  ("payerName", "payeeName", "payMethod", "payDateFmt", "traceNum", "bprAmount", "invoiceCount")])
steps.append((transform([mapset("csv", CSV_HEADER_TEMPLATE, variables=True)], mt, ms),
              "transform", None, None))

# LOOP over /valueList
loop_children = []
for label, src, rx, out in TXN_EXTRACTS:
    repl = DATE_REPL if label.startswith("Format") else "$1"
    loop_children.append((replace_invoke(label, src, rx, out, repl),
                          "invoke", ("replace", "pub.string:replace", "String", "string")))
mt = wrap_fields([pipe_field("csv")])
ms = wrap_fields([pipe_field(f) for f in ("csv", "invoiceNum", "invDateFmt", "invPaid", "invGross")])
loop_children.append((transform([mapset("csv", CSV_ROW_TEMPLATE, variables=True)], mt, ms),
                      "transform", None))
loop_xml = ('<LOOP NAME="AppendInvoiceRows" IN-ARRAY="/valueList" MAX-THREADS="1" PARALLEL-ERROR-HANDLING="reportError">\n'
            '  <COMMENT></COMMENT>\n'
            '  <!-- nodes -->\n'
            + "\n".join(c[0] for c in loop_children) + "\n"
            '</LOOP>')
steps.append((loop_xml, "loop", None, loop_children))

# trailer + status
mt = wrap_fields([pipe_field("csv"), pipe_field("status")])
ms = wrap_fields([pipe_field("csv"), pipe_field("bprAmount")])
steps.append((transform([mapset("csv", CSV_TRAILER_TEMPLATE, variables=True),
                         mapset("status", "CONVERTED_820_EXCEL", variables=False)], mt, ms),
              "transform", None, None))

# debugLog
mt = wrap_fields([svc_field("message"), svc_field("function"), svc_field("level")], "debugLogInput")
ms = wrap_fields([pipe_field("traceNum"), pipe_field("invoiceCount"), pipe_field("bprAmount")])
inp = "\n".join([mapset("message", DEBUG_MSG, variables=True),
                 mapset("function", SVC, variables=False),
                 mapset("level", "Info", variables=False)])
steps.append((invoke("LogCompletion", "pub.flow:debugLog", inp, mt, ms),
              "invoke", ("debugLog", "pub.flow:debugLog", "Flow", "flow"), None))

flow_xml = ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<FLOW VERSION="3.2" CLEANUP="true">\n'
            '  <COMMENT></COMMENT>\n'
            '  <!-- nodes -->\n'
            + "\n".join(s[0] for s in steps) + "\n"
            '</FLOW>')

# ---------------------------------------------------------------- hints
hints = []
seq = {"n": 0}

def hint_invoke(path, disp, svcname, gdisp, gname):
    seq["n"] += 1
    idx = path.split("/")[-1]
    dotted = ".".join(path.strip("/").split("/"))
    return (f'<record name="{path}" javaclass="com.wm.util.Values">\n'
            '  <null name="name"/>\n'
            '  <value name="itemType">SERVICES</value>\n'
            '  <record name="serviceInfo" javaclass="com.wm.util.Values">\n'
            f'    <value name="groupDisplayName">{gdisp}</value>\n'
            f'    <value name="groupName">{gname}</value>\n'
            '    <null name="services"/>\n'
            '  </record>\n'
            '  <record name="serviceName" javaclass="com.wm.util.Values">\n'
            f'    <value name="displayName">{disp}</value>\n'
            f'    <value name="serviceName">{svcname}</value>\n'
            '    <value name="transformerSupport">true</value>\n'
            '  </record>\n'
            f'  <number name="ui_step_index" type="Long">{seq["n"]}</number>\n'
            f'  <value name="nodePathNew">{dotted}</value>\n'
            f'  <number name="nodeIndex" type="Long">{idx}</number>\n'
            f'  <number name="lineNumberForDebug" type="Long">{seq["n"]}</number>\n'
            '  <null name="mapSet"/>\n'
            '  <null name="outputMapSet"/>\n'
            '</record>\n'
            f'<null name="{path}/0"/>')

def hint_transform(path):
    seq["n"] += 1
    idx = path.split("/")[-1]
    dotted = ".".join(path.strip("/").split("/"))
    return (f'<record name="{path}" javaclass="com.wm.util.Values">\n'
            '  <value name="itemType">CONTROLS</value>\n'
            '  <value name="name">TRANSFORM</value>\n'
            f'  <number name="ui_step_index" type="Long">{seq["n"]}</number>\n'
            f'  <value name="nodePathNew">{dotted}</value>\n'
            f'  <number name="nodeIndex" type="Long">{idx}</number>\n'
            f'  <number name="lineNumberForDebug" type="Long">{seq["n"]}</number>\n'
            '  <array name="map" type="object" depth="1" javaclass="java.lang.Object"></array>\n'
            '  <array name="mapSet" type="object" depth="1" javaclass="java.lang.Object"></array>\n'
            '  <array name="mapDelete" type="object" depth="1" javaclass="java.lang.Object"></array>\n'
            '  <array name="transformer" type="object" depth="1" javaclass="java.lang.Object"></array>\n'
            '  <array name="lookupTransformer" type="object" depth="1" javaclass="java.lang.Object"></array>\n'
            '</record>\n'
            f'<null name="{path}/0"/>')

def hint_loop(path):
    seq["n"] += 1
    idx = path.split("/")[-1]
    dotted = ".".join(path.strip("/").split("/"))
    return (f'<record name="{path}" javaclass="com.wm.util.Values">\n'
            '  <value name="name">REPEAT</value>\n'
            '  <value name="itemType">CONTROLS</value>\n'
            '  <record name="in_array" javaclass="com.wm.util.Values">\n'
            '    <value name="name">/valueList</value>\n'
            '    <value name="field_name">valueList</value>\n'
            '    <value name="type">string</value>\n'
            '    <value name="dim">1</value>\n'
            '    <Boolean name="isLink">false</Boolean>\n'
            '    <null name="wrapper_type"/>\n'
            '  </record>\n'
            f'  <value name="nodePathNew">{dotted}</value>\n'
            f'  <number name="nodeIndex" type="Long">{idx}</number>\n'
            f'  <number name="lineNumberForDebug" type="Long">{seq["n"]}</number>\n'
            '</record>')

def hint_minimal(path, name):
    return (f'<record name="{path}" javaclass="com.wm.util.Values">\n'
            f'  <value name="name">{name}</value>\n'
            '  <value name="itemType">CONTROLS</value>\n'
            '</record>')

for i, (xml, kind, info, children) in enumerate(steps):
    path = f"/{i}"
    if kind == "invoke":
        hints.append(hint_invoke(path, info[0], info[1], info[2], info[3]))
    elif kind == "transform":
        hints.append(hint_transform(path))
    elif kind == "branch":
        hints.append(hint_minimal(path, "BRANCH"))
        hints.append(f'<null name="{path}/0"/>')
        hints.append(f'<null name="{path}/0/0"/>')
        hints.append(f'<null name="{path}/1"/>')
        hints.append(hint_minimal(f"{path}/1/0", "EXIT"))
        hints.append(f'<null name="{path}/1/0/0"/>')
    elif kind == "loop":
        hints.append(hint_loop(path))
        for j, (cxml, ckind, cinfo) in enumerate(children):
            cpath = f"{path}/{j}"
            if ckind == "invoke":
                hints.append(hint_invoke(cpath, cinfo[0], cinfo[1], cinfo[2], cinfo[3]))
            else:
                hints.append(hint_transform(cpath))

step_hints = "\n".join(hints)
sig_in_fields = svc_field("edi820", larger=True)
sig_out_fields = "\n".join([svc_field("csv", larger=True), svc_field("status")])

node_ndf = f'''<?xml version="1.0" encoding="UTF-8"?>
<Values version="2.0">
  <value name="svc_type">flow</value>
  <value name="svc_subtype">unknown</value>
  <value name="svc_sigtype">unknown</value>
  <record name="svc_sig" javaclass="com.wm.util.Values">
    <record name="sig_in" javaclass="com.wm.util.Values">
      <value name="node_type">record</value>
      <value name="node_subtype">unknown</value>
      <value name="is_public">false</value>
      <value name="field_name"></value>
      <value name="field_type">record</value>
      <value name="field_dim">0</value>
      <value name="nillable">true</value>
      <value name="form_qualified">false</value>
      <value name="is_global">false</value>
      <array name="rec_fields" type="record" depth="1">
{sig_in_fields}
      </array>
    </record>
    <record name="sig_out" javaclass="com.wm.util.Values">
      <value name="node_type">record</value>
      <value name="node_subtype">unknown</value>
      <value name="is_public">false</value>
      <value name="field_name"></value>
      <value name="field_type">record</value>
      <value name="field_dim">0</value>
      <value name="nillable">true</value>
      <value name="form_qualified">false</value>
      <value name="is_global">false</value>
      <array name="rec_fields" type="record" depth="1">
{sig_out_fields}
      </array>
    </record>
  </record>
  <record name="node_hints" javaclass="com.wm.util.Values">
    <value name="createdBy">{USER}</value>
    <value name="createdDate">{NOW_MS}</value>
    <value name="lastModifiedBy">{USER}</value>
    <value name="lastModifiedDate">{NOW_MS}</value>
    <value name="displayName">{SVC}</value>
    <value name="description">X12 EDI 820 (payment order/remittance advice) -&gt; Excel-openable CSV. Validates ST*820 + BPR, extracts payment header, one row per RMR invoice, TOTAL row. Works for any number of invoices. DAFS-safe.</value>
    <record name="stepNodeHints" javaclass="com.wm.util.Values">
{step_hints}
    </record>
  </record>
</Values>
'''

os.makedirs(SVC_DIR, exist_ok=True)
with open(os.path.join(SVC_DIR, "flow.xml"), "w") as f:
    f.write(flow_xml)
with open(os.path.join(SVC_DIR, "node.ndf"), "w") as f:
    f.write(node_ndf)
minidom.parse(os.path.join(SVC_DIR, "flow.xml"))
minidom.parse(os.path.join(SVC_DIR, "node.ndf"))
print(f"OK  wrote + validated flow.xml and node.ndf ({len(steps)} top-level steps, {len(hints)} hint entries)")

# ---------------------------------------------------------------- simulation
def sim_replace(s, rx, repl):
    return re.sub(rx, repl.replace("$1-$2-$3", r"\1-\2-\3").replace("$1", r"\1"), s)

def simulate(edi):
    for label_expr, fail in VALIDATIONS:
        rx = label_expr.split("/")[1]
        if not re.search(rx, edi):
            raise ValueError(fail)
    pipe = {}
    for label, src, rx, out in HDR_EXTRACTS:
        repl = DATE_REPL if label.startswith("Format") else "$1"
        pipe[out] = sim_replace(edi if src == "edi820" else pipe[src], rx, repl)
    blocks = [b for b in pipe["rmrRegion"].split("RMR*") if b.strip()]
    pipe["invoiceCount"] = str(len(blocks))
    def subst(t, p):
        for k in sorted(p, key=len, reverse=True):
            t = t.replace(f"%{k}%", p[k])
        return t
    pipe["csv"] = subst(CSV_HEADER_TEMPLATE, pipe)
    for blk in blocks:
        for label, src, rx, out in TXN_EXTRACTS:
            repl = DATE_REPL if label.startswith("Format") else "$1"
            pipe[out] = sim_replace(blk if src == "valueList" else pipe[src], rx, repl)
        pipe["csv"] = subst(CSV_ROW_TEMPLATE, pipe)
    return subst(CSV_TRAILER_TEMPLATE, pipe)

if __name__ == "__main__" or True:
    from decimal import Decimal
    edi = open(os.path.join(ROOT, "sample_820.edi")).read()
    csv_out = simulate(edi)
    with open(os.path.join(ROOT, "expected_820_output.csv"), "w") as f:
        f.write(csv_out + "\n")
    lines = csv_out.split("\n")
    rows = [l.split(",") for l in lines[lines.index("Invoice Number,Invoice Date,Amount Paid,Invoice Amount") + 1:] if l and not l.startswith("TOTAL")]
    assert len(rows) == 4, f"expected 4 invoice rows, got {len(rows)}"
    paid = [Decimal(r[2]) for r in rows]
    assert sum(paid) == Decimal("61750.50"), f"sum {sum(paid)} != BPR 61750.50"
    assert "Payer,COBB COUNTY SCHOOL DISTRICT" in csv_out
    assert "Payee,LIMA INDUSTRIAL GROUP" in csv_out
    assert "Payment Method,ACH" in csv_out and "Payment Date,2026-07-01" in csv_out
    assert "Trace Number,CHK-20260701-4415" in csv_out
    assert "Invoice Count,4" in csv_out
    assert "INV-4002,2026-06-18,22500.50,23000.00" in csv_out  # early-pay discount row
    assert "TOTAL,,61750.50," in csv_out
    assert "%" not in csv_out
    print("OK  simulation: 4 invoice rows, sum == BPR amount 61750.50, dates formatted, summary block correct")
