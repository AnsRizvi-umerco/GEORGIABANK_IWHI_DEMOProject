#!/usr/bin/env python3
"""
Generator for the Pain001ToPacs008 Deploy Anywhere Flow Service (DAFS).

Emits ns/project/georgiabank_iwhi_demo/integrations/Pain001ToPacs008/{flow.xml,node.ndf}
from ONE step list (so flow.xml and stepNodeHints can't drift), validates both with
minidom, then SIMULATES the flow logic in Python against sample_pain001.xml and writes
expected_pacs008.xml + sample_pain001_b2b_payload.json.

DAFS constraints respected (handbook Parts III-IV):
- no pub.xml, no logCustomMessage/getLastError  -> pub.string only + debugLog
- no %array[N]% indexing                        -> pub.string:replace regex captures
- whole-array ops only                          -> tokenize + LOOP IN-ARRAY
"""
import base64, json, os, re, time
from xml.dom import minidom

ROOT = os.path.dirname(os.path.abspath(__file__))
SVC = "Pain001ToPacs008"
SVC_DIR = os.path.join(ROOT, "ns", "project", "georgiabank_iwhi_demo", "integrations", SVC)
USER = "ans.rizvi@umerco.com"
NOW_MS = int(time.time() * 1000)

def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

# ----------------------------------------------------------------------------
# Regexes (Java replaceAll semantics; $1 backreference; (?s) = DOTALL)
# ----------------------------------------------------------------------------
HDR_EXTRACTS = [  # (label, out_field, regex)  -- all run against painXml
    ("ExtractMsgId",        "msgId",        r'(?s).*?<MsgId>([^<]*)</MsgId>.*'),
    ("ExtractCreDtTm",      "creDtTm",      r'(?s).*?<CreDtTm>([^<]*)</CreDtTm>.*'),
    ("ExtractNbOfTxs",      "nbOfTxs",      r'(?s).*?<NbOfTxs>([^<]*)</NbOfTxs>.*'),
    ("ExtractCtrlSum",      "ctrlSum",      r'(?s).*?<CtrlSum>([^<]*)</CtrlSum>.*'),
    ("ExtractReqdExctnDt",  "reqdExctnDt",  r'(?s).*?<ReqdExctnDt>\s*<Dt>([^<]*)</Dt>.*'),
    ("ExtractDbtrNm",       "dbtrNm",       r'(?s).*?<Dbtr>\s*<Nm>([^<]*)</Nm>.*'),
    ("ExtractDbtrAcctId",   "dbtrAcctId",   r'(?s).*?<DbtrAcct>\s*<Id>\s*<Othr>\s*<Id>([^<]*)</Id>.*'),
    ("ExtractDbtrAgtMmbId", "dbtrAgtMmbId", r'(?s).*?<DbtrAgt>\s*<FinInstnId>\s*<ClrSysMmbId>\s*<MmbId>([^<]*)</MmbId>.*'),
    ("ExtractTxnRegion",    "txnRegion",    r'(?s)^.*?<CdtTrfTxInf>(.*)'),
]
TXN_EXTRACTS = [  # run against the current loop element (scalar valueList)
    ("TxInstrId",       "instrId",      r'(?s).*?<InstrId>([^<]*)</InstrId>.*'),
    ("TxEndToEndId",    "endToEndId",   r'(?s).*?<EndToEndId>([^<]*)</EndToEndId>.*'),
    ("TxAmount",        "txAmt",        r'(?s).*?<InstdAmt[^>]*>([^<]*)</InstdAmt>.*'),
    ("TxCurrency",      "txCcy",        r'(?s).*?<InstdAmt Ccy="([^"]*)".*'),
    ("TxCdtrAgtNm",     "cdtrAgtNm",    r'(?s).*?<CdtrAgt>\s*<FinInstnId>\s*<Nm>([^<]*)</Nm>.*'),
    ("TxCdtrAgtMmbId",  "cdtrAgtMmbId", r'(?s).*?<CdtrAgt>.*?<MmbId>([^<]*)</MmbId>.*'),
    ("TxCdtrNm",        "cdtrNm",       r'(?s).*?<Cdtr>\s*<Nm>([^<]*)</Nm>.*'),
    ("TxCdtrAcctId",    "cdtrAcctId",   r'(?s).*?<CdtrAcct>\s*<Id>\s*<Othr>\s*<Id>([^<]*)</Id>.*'),
    ("TxRmtDesc",       "rmtDesc",      r'(?s).*?<(?:Ustrd|AddtlRmtInf)>([^<]*)<.*'),
]

HEADER_TEMPLATE = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<Document xmlns="urn:iso:std:iso:20022:tech:xsd:pacs.008.001.08">\n'
    '<FIToFICstmrCdtTrf>\n'
    '<GrpHdr>\n'
    '<MsgId>%msgId%-P8</MsgId>\n'
    '<CreDtTm>%creDtTm%</CreDtTm>\n'
    '<NbOfTxs>%nbOfTxs%</NbOfTxs>\n'
    '<TtlIntrBkSttlmAmt Ccy="USD">%ctrlSum%</TtlIntrBkSttlmAmt>\n'
    '<IntrBkSttlmDt>%reqdExctnDt%</IntrBkSttlmDt>\n'
    '<SttlmInf><SttlmMtd>CLRG</SttlmMtd><ClrSys><Cd>FDW</Cd></ClrSys></SttlmInf>\n'
    '<InstgAgt><FinInstnId><ClrSysMmbId><ClrSysId><Cd>USABA</Cd></ClrSysId>'
    '<MmbId>%dbtrAgtMmbId%</MmbId></ClrSysMmbId></FinInstnId></InstgAgt>\n'
    '</GrpHdr>'
)
TXN_TEMPLATE = (
    '%pacs008%\n'
    '<CdtTrfTxInf>\n'
    '<PmtId><InstrId>%instrId%</InstrId><EndToEndId>%endToEndId%</EndToEndId></PmtId>\n'
    '<IntrBkSttlmAmt Ccy="%txCcy%">%txAmt%</IntrBkSttlmAmt>\n'
    '<ChrgBr>SLEV</ChrgBr>\n'
    '<Dbtr><Nm>%dbtrNm%</Nm></Dbtr>\n'
    '<DbtrAcct><Id><Othr><Id>%dbtrAcctId%</Id></Othr></Id></DbtrAcct>\n'
    '<DbtrAgt><FinInstnId><ClrSysMmbId><MmbId>%dbtrAgtMmbId%</MmbId></ClrSysMmbId></FinInstnId></DbtrAgt>\n'
    '<CdtrAgt><FinInstnId><Nm>%cdtrAgtNm%</Nm><ClrSysMmbId><MmbId>%cdtrAgtMmbId%</MmbId></ClrSysMmbId></FinInstnId></CdtrAgt>\n'
    '<Cdtr><Nm>%cdtrNm%</Nm></Cdtr>\n'
    '<CdtrAcct><Id><Othr><Id>%cdtrAcctId%</Id></Othr></Id></CdtrAcct>\n'
    '<RmtInf><Ustrd>%rmtDesc%</Ustrd></RmtInf>\n'
    '</CdtTrfTxInf>'
)
TRAILER_TEMPLATE = '%pacs008%\n</FIToFICstmrCdtTrf>\n</Document>'
DEBUG_MSG = 'pain.001 %msgId% translated to pacs.008: %nbOfTxs% transactions, total %ctrlSum% USD'

# ----------------------------------------------------------------------------
# node.ndf field-declaration emitters (handbook section 3 / 6 shapes)
# ----------------------------------------------------------------------------
def field_decl(name, ftype="string", dim=0, node_type="unknown", hints=False, rec_children=None):
    parts = ['<record javaclass="com.wm.util.Values">',
             f'  <value name="node_type">{node_type}</value>',
             '  <value name="node_subtype">unknown</value>']
    if hints:
        parts += ['  <value name="node_comment"></value>',
                  '  <record name="node_hints" javaclass="com.wm.util.Values">',
                  '    <value name="field_largerEditor">false</value>',
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
    """MAPTARGET/MAPSOURCE body: Values wrapper holding a record of field decls."""
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

def svc_field(name, ftype="string"):          # a service's own input parameter
    return field_decl(name, ftype, node_type="field", hints=True)

def pipe_field(name, ftype="string", dim=0):  # pipeline-local variable
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
    body = "\n".join(sets)
    return ('<MAP NAME="TRANSFORM" TIMEOUT="" MODE="STANDALONE">\n'
            '  <COMMENT></COMMENT>\n'
            f'  <MAPTARGET>\n{maptarget}\n  </MAPTARGET>\n'
            f'  <MAPSOURCE>\n{mapsource}\n  </MAPSOURCE>\n'
            '  <!-- nodes -->\n'
            f'{body}\n'
            '</MAP>')

def replace_invoke(label, src_field, regex, out_field):
    mt = wrap_fields([svc_field("inString"), svc_field("searchString"),
                      svc_field("replaceString"), svc_field("useRegex")], "replaceInput")
    ms = wrap_fields([pipe_field(src_field)])
    inp = "\n".join([
        mapcopy(src_field, "inString"),
        mapset("searchString", regex, variables=False),
        mapset("replaceString", "$1", variables=False),
        mapset("useRegex", "true", variables=False),
    ])
    out = mapcopy("value", out_field)
    return invoke(label, "pub.string:replace", inp, mt, ms, out)

# ----------------------------------------------------------------------------
# Build the step list -> flow.xml fragments + hint metadata
# ----------------------------------------------------------------------------
steps = []  # (xml, hint_kind, hint_info, children) -- top-level order

# 0: base64Decode  (request/content -> value)
mt = wrap_fields([svc_field("string")], "base64DecodeInput")
ms = wrap_fields([field_decl("request", "record", node_type="record",
                             rec_children=[svc_field("content"), svc_field("encoding")])])
steps.append((invoke("DecodeB2BContent", "pub.string:base64Decode",
                     mapset("string", "%request/content%", variables=True), mt, ms),
              "invoke", ("base64Decode", "pub.string:base64Decode", "String", "string"), None))

# 1: bytesToString (value -> painXml)
mt = wrap_fields([svc_field("bytes", "object"), svc_field("encoding")], "bytesToStringInput")
ms = wrap_fields([pipe_field("value", "object")])
inp = "\n".join([mapcopy("value", "bytes"), mapset("encoding", "UTF-8", variables=False)])
steps.append((invoke("BytesToXmlString", "pub.string:bytesToString", inp, mt, ms,
                     mapcopy("string", "painXml")),
              "invoke", ("bytesToString", "pub.string:bytesToString", "String", "string"), None))

# 2..10: header extracts
for label, out, rx in HDR_EXTRACTS:
    steps.append((replace_invoke(label, "painXml", rx, out),
                  "invoke", ("replace", "pub.string:replace", "String", "string"), None))

# 11: tokenize txnRegion by <CdtTrfTxInf> -> valueList
mt = wrap_fields([svc_field("inString"), svc_field("delim"), svc_field("useDelimsAsSet")], "tokenizeInput")
ms = wrap_fields([pipe_field("txnRegion")])
inp = "\n".join([mapcopy("txnRegion", "inString"),
                 mapset("delim", "<CdtTrfTxInf>", variables=False),
                 mapset("useDelimsAsSet", "false", variables=False)])
steps.append((invoke("SplitTransactions", "pub.string:tokenize", inp, mt, ms),
              "invoke", ("tokenize", "pub.string:tokenize", "String", "string"), None))

# 12: TRANSFORM init pacs008 header
mt = wrap_fields([pipe_field("pacs008")])
ms = wrap_fields([pipe_field(f) for f in ("msgId", "creDtTm", "nbOfTxs", "ctrlSum", "reqdExctnDt", "dbtrAgtMmbId")])
steps.append((transform([mapset("pacs008", HEADER_TEMPLATE, variables=True)], mt, ms),
              "transform", None, None))

# 13: LOOP over /valueList
loop_children = []
for label, out, rx in TXN_EXTRACTS:
    loop_children.append((replace_invoke(label, "valueList", rx, out),
                          "invoke", ("replace", "pub.string:replace", "String", "string")))
mt = wrap_fields([pipe_field("pacs008")])
ms = wrap_fields([pipe_field(f) for f in
                  ("pacs008", "instrId", "endToEndId", "txAmt", "txCcy", "dbtrNm", "dbtrAcctId",
                   "dbtrAgtMmbId", "cdtrAgtNm", "cdtrAgtMmbId", "cdtrNm", "cdtrAcctId", "rmtDesc")])
loop_children.append((transform([mapset("pacs008", TXN_TEMPLATE, variables=True)], mt, ms),
                      "transform", None))
loop_xml = ('<LOOP NAME="AppendTransactions" IN-ARRAY="/valueList" MAX-THREADS="1" PARALLEL-ERROR-HANDLING="reportError">\n'
            '  <COMMENT></COMMENT>\n'
            '  <!-- nodes -->\n'
            + "\n".join(c[0] for c in loop_children) + "\n"
            '</LOOP>')
steps.append((loop_xml, "loop", None, loop_children))

# 14: TRANSFORM trailer + status
mt = wrap_fields([pipe_field("pacs008"), pipe_field("status")])
ms = wrap_fields([pipe_field("pacs008")])
steps.append((transform([mapset("pacs008", TRAILER_TEMPLATE, variables=True),
                         mapset("status", "TRANSLATED_PACS008", variables=False)], mt, ms),
              "transform", None, None))

# 15: debugLog
mt = wrap_fields([svc_field("message"), svc_field("function"), svc_field("level")], "debugLogInput")
ms = wrap_fields([pipe_field("msgId"), pipe_field("nbOfTxs"), pipe_field("ctrlSum")])
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

# ----------------------------------------------------------------------------
# stepNodeHints (one entry per step, doc order; leaves get /path/0 null)
# ----------------------------------------------------------------------------
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

for i, (xml, kind, info, children) in enumerate(steps):
    path = f"/{i}"
    if kind == "invoke":
        hints.append(hint_invoke(path, info[0], info[1], info[2], info[3]))
    elif kind == "transform":
        hints.append(hint_transform(path))
    elif kind == "loop":
        hints.append(hint_loop(path))
        for j, (cxml, ckind, cinfo) in enumerate(children):
            cpath = f"{path}/{j}"
            if ckind == "invoke":
                hints.append(hint_invoke(cpath, cinfo[0], cinfo[1], cinfo[2], cinfo[3]))
            else:
                hints.append(hint_transform(cpath))

step_hints = "\n".join(hints)

sig_in_fields = "\n".join([
    field_decl("metadata", "record", node_type="record", rec_children=[]),
    field_decl("request", "record", node_type="record",
               rec_children=[svc_field("content"), svc_field("encoding")]),
])
sig_out_fields = "\n".join([svc_field("pacs008"), svc_field("status")])

node_ndf = f'''<?xml version="1.0" encoding="UTF-8"?>
<Values version="2.0">
  <value name="svc_type">flow</value>
  <value name="svc_subtype">unknown</value>
  <value name="svc_sigtype">unknown</value>
  <record name="svc_sig" javaclass="com.wm.util.Values">
    <record name="sig_in" javaclass="com.wm.util.Values">
      <value name="node_type">record</value>
      <value name="node_subtype">unknown</value>
      <array name="rec_fields" type="record" depth="1">
{sig_in_fields}
      </array>
    </record>
    <record name="sig_out" javaclass="com.wm.util.Values">
      <value name="node_type">record</value>
      <value name="node_subtype">unknown</value>
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
    <value name="description">pain.001.001.09 -&gt; pacs.008.001.08 translation (GBC production pattern). DAFS-safe: pub.string regex extraction, LOOP accumulator build, works for any number of transactions.</value>
    <record name="stepNodeHints" javaclass="com.wm.util.Values">
{step_hints}
    </record>
  </record>
</Values>
'''

# ----------------------------------------------------------------------------
# Write + validate
# ----------------------------------------------------------------------------
os.makedirs(SVC_DIR, exist_ok=True)
with open(os.path.join(SVC_DIR, "flow.xml"), "w") as f:
    f.write(flow_xml)
with open(os.path.join(SVC_DIR, "node.ndf"), "w") as f:
    f.write(node_ndf)
minidom.parse(os.path.join(SVC_DIR, "flow.xml"))
minidom.parse(os.path.join(SVC_DIR, "node.ndf"))
print(f"OK  wrote + validated {SVC_DIR}/flow.xml and node.ndf")
print(f"    top-level steps: {len(steps)}; loop children: {len(loop_children)}; hints: {len(hints)}")

# ----------------------------------------------------------------------------
# SIMULATION -- replicate every step in Python against sample_pain001.xml
# ----------------------------------------------------------------------------
def java_replace_all(s, rx, repl):
    return re.sub(rx, repl.replace("$1", r"\1"), s)

with open(os.path.join(ROOT, "sample_pain001.xml")) as f:
    pain_xml = f.read()

# steps 0-1: base64 round trip (simulated: identity)
b64 = base64.b64encode(pain_xml.encode()).decode()
painXml = base64.b64decode(b64).decode()

pipe = {}
for _, out, rx in HDR_EXTRACTS:
    pipe[out] = java_replace_all(painXml, rx, "$1")

blocks = [b for b in pipe["txnRegion"].split("<CdtTrfTxInf>") if b.strip()]

def subst(template, p):
    out = template
    for k in sorted(p, key=len, reverse=True):
        out = out.replace(f"%{k}%", p[k])
    return out

pipe["pacs008"] = subst(HEADER_TEMPLATE, pipe)
for blk in blocks:
    for _, out, rx in TXN_EXTRACTS:
        pipe[out] = java_replace_all(blk, rx, "$1")
    pipe["pacs008"] = subst(TXN_TEMPLATE, pipe)
pipe["pacs008"] = subst(TRAILER_TEMPLATE, pipe)
pacs = pipe["pacs008"]

with open(os.path.join(ROOT, "expected_pacs008.xml"), "w") as f:
    f.write(pacs + "\n")

# assertions
minidom.parseString(pacs)
assert pacs.count("<CdtTrfTxInf>") == 5, "expected 5 transactions"
for amt in ("75000.00", "42500.00", "28000.00", "22000.00", "20000.00"):
    assert f'>{amt}</IntrBkSttlmAmt>' in pacs, f"missing amount {amt}"
assert "<MsgId>GBC-20260506-PAYROLL-001-P8</MsgId>" in pacs
assert '>187500.00</TtlIntrBkSttlmAmt>' in pacs
assert "<IntrBkSttlmDt>2026-05-06</IntrBkSttlmDt>" in pacs
assert "<MmbId>021000021</MmbId>" in pacs and "<MmbId>062000019</MmbId>" in pacs
assert "May 2026 steel supply settlement" in pacs        # tx1 Strd/AddtlRmtInf
assert "May 2026 office supplies" in pacs                # tx5 Ustrd
assert pacs.count("<Dbtr><Nm>Lima Industrial Group</Nm></Dbtr>") == 5
assert "%" not in re.sub(r"%[0-9]", "%%", pacs), "unresolved %substitution% in output"
print("OK  simulation: 5 txns, all amounts, header, remittance verified; output is well-formed XML")

# B2B/Run-dialog envelope
with open(os.path.join(ROOT, "sample_pain001_b2b_payload.json"), "w") as f:
    json.dump({"metadata": {}, "request": {"content": b64, "encoding": "base64", "type": "XML"}}, f, indent=2)
print("OK  wrote expected_pacs008.xml and sample_pain001_b2b_payload.json")
