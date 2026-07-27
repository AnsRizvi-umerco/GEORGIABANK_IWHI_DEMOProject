#!/usr/bin/env python3
"""
Generator for the Pain001ToPacs008 Deploy Anywhere Flow Service (DAFS).

Signature (v2 -- simplified per live-test feedback):
    sig_in : painXml (String)  -- the raw pain.001.001.09 XML, pasted straight in
    sig_out: pacs008 (String)  -- pretty-printed pacs.008.001.08
             status  (String)  -- TRANSLATED_PACS008

Flow: BRANCH validate (pain.001.001.09 or EXIT FAILURE) -> 9x regex header extract
      -> tokenize <CdtTrfTxInf> -> TRANSFORM GrpHdr -> LOOP txns -> trailer -> debugLog

DAFS constraints respected (handbook Parts III-IV): no pub.xml, no getLastError,
no %array[N]% indexing; validation via BRANCH regex label + EXIT (Part II 14.2).
Emits flow.xml + node.ndf from ONE step list, validates with minidom, then simulates
the whole flow in Python against sample_pain001.xml -> expected_pacs008.xml.
"""
import os, re, time
from xml.dom import minidom

ROOT = os.path.dirname(os.path.abspath(__file__))
SVC = "Pain001ToPacs008"
SVC_DIR = os.path.join(ROOT, "ns", "project", "georgiabank_iwhi_demo", "integrations", SVC)
USER = "ans.rizvi@umerco.com"
NOW_MS = int(time.time() * 1000)

def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

# ----------------------------------------------------------------------------
# Regexes (Java replaceAll; $1 backref; (?s)=DOTALL)
# ----------------------------------------------------------------------------
HDR_EXTRACTS = [
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
TXN_EXTRACTS = [
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

# Pretty-printed pacs.008 templates (2-space indents preserved in MAPSET literals)
HEADER_TEMPLATE = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<Document xmlns="urn:iso:std:iso:20022:tech:xsd:pacs.008.001.08">\n'
    '  <FIToFICstmrCdtTrf>\n'
    '    <GrpHdr>\n'
    '      <MsgId>%msgId%-P8</MsgId>\n'
    '      <CreDtTm>%creDtTm%</CreDtTm>\n'
    '      <NbOfTxs>%nbOfTxs%</NbOfTxs>\n'
    '      <TtlIntrBkSttlmAmt Ccy="USD">%ctrlSum%</TtlIntrBkSttlmAmt>\n'
    '      <IntrBkSttlmDt>%reqdExctnDt%</IntrBkSttlmDt>\n'
    '      <SttlmInf>\n'
    '        <SttlmMtd>CLRG</SttlmMtd>\n'
    '        <ClrSys>\n'
    '          <Cd>FDW</Cd>\n'
    '        </ClrSys>\n'
    '      </SttlmInf>\n'
    '      <InstgAgt>\n'
    '        <FinInstnId>\n'
    '          <ClrSysMmbId>\n'
    '            <ClrSysId>\n'
    '              <Cd>USABA</Cd>\n'
    '            </ClrSysId>\n'
    '            <MmbId>%dbtrAgtMmbId%</MmbId>\n'
    '          </ClrSysMmbId>\n'
    '        </FinInstnId>\n'
    '      </InstgAgt>\n'
    '    </GrpHdr>'
)
TXN_TEMPLATE = (
    '%pacs008%\n'
    '    <CdtTrfTxInf>\n'
    '      <PmtId>\n'
    '        <InstrId>%instrId%</InstrId>\n'
    '        <EndToEndId>%endToEndId%</EndToEndId>\n'
    '      </PmtId>\n'
    '      <IntrBkSttlmAmt Ccy="%txCcy%">%txAmt%</IntrBkSttlmAmt>\n'
    '      <ChrgBr>SLEV</ChrgBr>\n'
    '      <Dbtr>\n'
    '        <Nm>%dbtrNm%</Nm>\n'
    '      </Dbtr>\n'
    '      <DbtrAcct>\n'
    '        <Id>\n'
    '          <Othr>\n'
    '            <Id>%dbtrAcctId%</Id>\n'
    '          </Othr>\n'
    '        </Id>\n'
    '      </DbtrAcct>\n'
    '      <DbtrAgt>\n'
    '        <FinInstnId>\n'
    '          <ClrSysMmbId>\n'
    '            <MmbId>%dbtrAgtMmbId%</MmbId>\n'
    '          </ClrSysMmbId>\n'
    '        </FinInstnId>\n'
    '      </DbtrAgt>\n'
    '      <CdtrAgt>\n'
    '        <FinInstnId>\n'
    '          <Nm>%cdtrAgtNm%</Nm>\n'
    '          <ClrSysMmbId>\n'
    '            <MmbId>%cdtrAgtMmbId%</MmbId>\n'
    '          </ClrSysMmbId>\n'
    '        </FinInstnId>\n'
    '      </CdtrAgt>\n'
    '      <Cdtr>\n'
    '        <Nm>%cdtrNm%</Nm>\n'
    '      </Cdtr>\n'
    '      <CdtrAcct>\n'
    '        <Id>\n'
    '          <Othr>\n'
    '            <Id>%cdtrAcctId%</Id>\n'
    '          </Othr>\n'
    '        </Id>\n'
    '      </CdtrAcct>\n'
    '      <RmtInf>\n'
    '        <Ustrd>%rmtDesc%</Ustrd>\n'
    '      </RmtInf>\n'
    '    </CdtTrfTxInf>'
)
TRAILER_TEMPLATE = '%pacs008%\n  </FIToFICstmrCdtTrf>\n</Document>'
DEBUG_MSG = 'pain.001 %msgId% translated to pacs.008: %nbOfTxs% transactions, total %ctrlSum% USD'
VALIDATE_LABEL = r'painXml = /pain\.001\.001\.09/'
VALIDATE_FAIL = 'Input is not a pain.001.001.09 document - translation aborted'

# ----------------------------------------------------------------------------
# node.ndf field declarations (handbook 3 / 6)
# ----------------------------------------------------------------------------
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
    return invoke(label, "pub.string:replace", inp, mt, ms, mapcopy("value", out_field))

# ----------------------------------------------------------------------------
# Step list
# ----------------------------------------------------------------------------
steps = []  # (xml, kind, info, children)

# 0: BRANCH validation -- pain.001.001.09 or EXIT FAILURE (Part II 14.2 pattern)
branch_xml = (
    '<BRANCH LABELEXPRESSIONS="true">\n'
    '  <COMMENT></COMMENT>\n'
    '  <!-- nodes -->\n'
    f'<SEQUENCE NAME="{VALIDATE_LABEL}" EXIT-ON="FAILURE">\n'
    '  <COMMENT></COMMENT>\n'
    '</SEQUENCE>\n'
    '<SEQUENCE NAME="$default" EXIT-ON="FAILURE">\n'
    '  <COMMENT></COMMENT>\n'
    f'<EXIT NAME="EXIT" FROM="$parent" SIGNAL="FAILURE" FAILURE-MESSAGE="{VALIDATE_FAIL}">\n'
    '  <COMMENT></COMMENT>\n'
    '</EXIT>\n'
    '</SEQUENCE>\n'
    '</BRANCH>'
)
steps.append((branch_xml, "branch", None, None))

# 1..9: header extracts straight off painXml
for label, out, rx in HDR_EXTRACTS:
    steps.append((replace_invoke(label, "painXml", rx, out),
                  "invoke", ("replace", "pub.string:replace", "String", "string"), None))

# 10: tokenize txnRegion -> valueList
mt = wrap_fields([svc_field("inString"), svc_field("delim"), svc_field("useDelimsAsSet")], "tokenizeInput")
ms = wrap_fields([pipe_field("txnRegion")])
inp = "\n".join([mapcopy("txnRegion", "inString"),
                 mapset("delim", "<CdtTrfTxInf>", variables=False),
                 mapset("useDelimsAsSet", "false", variables=False)])
steps.append((invoke("SplitTransactions", "pub.string:tokenize", inp, mt, ms),
              "invoke", ("tokenize", "pub.string:tokenize", "String", "string"), None))

# 11: TRANSFORM init GrpHdr
mt = wrap_fields([pipe_field("pacs008")])
ms = wrap_fields([pipe_field(f) for f in ("msgId", "creDtTm", "nbOfTxs", "ctrlSum", "reqdExctnDt", "dbtrAgtMmbId")])
steps.append((transform([mapset("pacs008", HEADER_TEMPLATE, variables=True)], mt, ms),
              "transform", None, None))

# 12: LOOP over /valueList
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

# 13: trailer + status
mt = wrap_fields([pipe_field("pacs008"), pipe_field("status")])
ms = wrap_fields([pipe_field("pacs008")])
steps.append((transform([mapset("pacs008", TRAILER_TEMPLATE, variables=True),
                         mapset("status", "TRANSLATED_PACS008", variables=False)], mt, ms),
              "transform", None, None))

# 14: debugLog
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
# stepNodeHints
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

def hint_minimal(path, name):  # BRANCH / EXIT (Part I 4, Part II 15)
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
        # BRANCH record; case SEQUENCEs are null placeholders; EXIT gets a record
        hints.append(hint_minimal(path, "BRANCH"))
        hints.append(f'<null name="{path}/0"/>')      # match-case SEQUENCE
        hints.append(f'<null name="{path}/0/0"/>')    # its empty body
        hints.append(f'<null name="{path}/1"/>')      # $default SEQUENCE
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
sig_in_fields = svc_field("painXml", larger=True)
sig_out_fields = "\n".join([svc_field("pacs008", larger=True), svc_field("status")])

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
    <value name="description">pain.001.001.09 -&gt; pacs.008.001.08 translation (GBC production pattern). Input: painXml (raw pain.001 XML). Output: pretty-printed pacs008 + status. Validates input via BRANCH, works for any number of transactions. DAFS-safe.</value>
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
print(f"OK  wrote + validated flow.xml and node.ndf ({len(steps)} top-level steps, {len(hints)} hint entries)")

# ----------------------------------------------------------------------------
# SIMULATION against sample_pain001.xml
# ----------------------------------------------------------------------------
def java_replace_all(s, rx, repl):
    return re.sub(rx, repl.replace("$1", r"\1"), s)

with open(os.path.join(ROOT, "sample_pain001.xml")) as f:
    painXml = f.read()

# step 0: BRANCH validation
assert re.search(r'pain\.001\.001\.09', painXml), "validation BRANCH would EXIT FAILURE"

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

minidom.parseString(pacs)  # well-formed check
assert pacs.count("<CdtTrfTxInf>") == 5
for amt in ("75000.00", "42500.00", "28000.00", "22000.00", "20000.00"):
    assert f'>{amt}</IntrBkSttlmAmt>' in pacs, f"missing {amt}"
assert "<MsgId>GBC-20260506-PAYROLL-001-P8</MsgId>" in pacs
assert '>187500.00</TtlIntrBkSttlmAmt>' in pacs
assert "<IntrBkSttlmDt>2026-05-06</IntrBkSttlmDt>" in pacs
assert "<MmbId>021000021</MmbId>" in pacs and "<MmbId>062000019</MmbId>" in pacs
assert "May 2026 steel supply settlement" in pacs and "May 2026 office supplies" in pacs
assert pacs.count("<Nm>Lima Industrial Group</Nm>") == 5
assert "%" not in pacs, "unresolved %substitution% in output"
# negative test: a non-pain.001.001.09 doc must fail the BRANCH
assert not re.search(r'pain\.001\.001\.09', "<Document xmlns='urn:iso:std:iso:20022:tech:xsd:pain.001.001.03'/>".replace("001.03", "001.03"))
print("OK  simulation: validation branch, 5 txns, all amounts, formatting verified; output well-formed")
