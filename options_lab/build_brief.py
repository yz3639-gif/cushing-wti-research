"""Build the one-page English desk brief with an explicit evidence boundary."""
from pathlib import Path
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.enums import TA_LEFT
from pypdf import PdfReader

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"options_lab_runs/deliverables"

def build():
    OUT.mkdir(parents=True,exist_ok=True)
    path=OUT/"WTI_Options_Desk_Brief.pdf"
    ink=colors.HexColor("#182B36"); teal=colors.HexColor("#146C71")
    style=ParagraphStyle("body",fontName="Helvetica",fontSize=9.8,leading=14,textColor=ink,spaceAfter=9)
    small=ParagraphStyle("small",parent=style,fontSize=8.5,leading=11)
    label=ParagraphStyle("label",parent=style,fontName="Helvetica-Bold",fontSize=10.5,spaceBefore=11,spaceAfter=5,textColor=teal)
    title=ParagraphStyle("title",parent=style,fontName="Helvetica-Bold",fontSize=26,leading=31,spaceAfter=8)
    subtitle=ParagraphStyle("subtitle",parent=style,fontSize=12,leading=16,textColor=teal,spaceAfter=16)
    def p(text,kind=style):return Paragraph(text,kind)
    story=[p("WTI Options Desk",title),p("Editable volatility. Explainable quotes. Visible residual risk.",subtitle),p("A local Python / Streamlit research tool built alongside the Cushing inventory study. It connects an identified WTI calendar-spread option to pricing, indicative quotes, integer proxy hedges and full-repricing stress results.")]
    story += [p("01  ADJUST VOLATILITY WITHOUT LOSING THE AUDIT TRAIL",label),p("Market preserves calibrated observations. Draft is editable. Active drives calculations. Preview fixes the same market and portfolio; Apply publishes one complete result bundle. Manual mode retains the trader's changes through market refreshes; Follow market resumes accepted calibrations. Version history supports restoration.")]
    rows=[[p("CSO / Bachelier",small),p("Normal volatility: USD/bbl per square-root year.",small)],[p("European vanilla / Black-76",small),p("Lognormal volatility: annualized percentage.",small)]]
    table=Table(rows,colWidths=[176,328],hAlign="LEFT")
    table.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,-1),colors.HexColor("#ECF4F3")),("VALIGN",(0,0),(-1,-1),"TOP"),("LEFTPADDING",(0,0),(-1,-1),9),("RIGHTPADDING",(0,0),(-1,-1),9),("TOPPADDING",(0,0),(-1,-1),8),("BOTTOMPADDING",(0,0),(-1,-1),3)]))
    story.append(table)
    story += [p("02  SEE WHAT CHANGES AND WHY",label),p("A valid volatility change reprices premiums and Greeks, then revises modeled bid/ask using explicit costs, inventory and scenario-risk limits. Strike interpolation stays within one product, exact underlying/month pair and expiry. Missing coverage is unavailable; no extrapolated full surface is manufactured. Price-bound, monotonicity and convexity diagnostics are checks, not a global no-arbitrage guarantee.")]
    story += [p("03  HEDGE WHAT CAN BE HEDGED",label),p("With direct CSO offset disabled, a bounded integer optimizer proposes futures and European vanilla option lots. Compare unhedged, futures-only and futures-plus-vanilla outcomes under the same scenarios and costs. Every proposal includes quantities, direction, cost and solver status. Limited-search solutions are identified; they are not claimed to be optimal.")]
    callout=p("<b>The critical stress:</b> CSO volatility rises while vanilla volatility does not. Full repricing shows the remaining loss that a proxy hedge cannot remove.",style)
    box=Table([[callout]],colWidths=[504]); box.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,-1),colors.HexColor("#F7F1E5")),("BOX",(0,0),(-1,-1),.5,colors.HexColor("#D9C99F")),("LEFTPADDING",(0,0),(-1,-1),11),("TOPPADDING",(0,0),(-1,-1),10),("BOTTOMPADDING",(0,0),(-1,-1),3)]));story.append(box)
    story += [p("EVIDENCE & NEXT VALIDATION",label),p("The included demonstration uses explicitly labeled engineering fixtures. No authorized real CL + LC/LCE + 7A/B7A snapshot has yet been acquired. The 120-session study with the final 40 sessions held out, and 60-minute live-feed acceptance, remain pending. Numerical and software tests do not establish a market hedge advantage. No order routing or automated purchases are provided.",small)]
    def footer(canvas,doc):
        canvas.setStrokeColor(colors.HexColor("#D3DEDF"));canvas.line(54,43,558,43)
        canvas.setFont("Helvetica",8);canvas.setFillColor(teal);canvas.drawString(54,29,"Yuang (Anthony) Zuo  |  WTI Options Desk Lab  |  September 2026")
        canvas.drawRightString(558,29,"LOCAL RESEARCH PROTOTYPE")
    SimpleDocTemplate(str(path),pagesize=(612,792),leftMargin=54,rightMargin=54,topMargin=40,bottomMargin=55).build(story,onFirstPage=footer,onLaterPages=footer)
    assert len(PdfReader(path).pages)==1,"Brief exceeded one page"
    print(path)
    return path

if __name__=="__main__":build()
