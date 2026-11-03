"""Facility-owned PDF layouts for authored fictional records (no external fonts/assets)."""
from dataclasses import dataclass
from datetime import date
import re
try:
    from pdfminer.fontmetrics import FONT_METRICS
except ImportError:
    FONT_METRICS={}

FOOTER='Fictional demonstration document — not for clinical use.'
PATIENT_ID='DEMO-JD-19351101'

@dataclass(frozen=True)
class Facility:
    name:str
    address:str
    phone:str
    email:str
    colour:tuple
    layout:str

FACILITIES={
 'Harbourside Cardiology':Facility('Harbourside Cardiology','14 Example Wharf, Sydney NSW 2000','02 5550 0140 (fictional)','rooms@harbourside.example.invalid',(.14,.23,.34),'cardiology'),
 'Sydney Aged Care':Facility('Sydney Aged Care','1 Demonstration Lane, Sydney NSW 2000','02 5550 0101 (fictional)','reception@sydneyagedcare.example.invalid',(.20,.28,.30),'gp'),
 'Example Community Audiology':Facility('Example Community Audiology','2 Demonstration Road, Sydney NSW 2000','02 5550 0142 (fictional)','hearing@communityaudiology.example.invalid',(.12,.36,.39),'audiology'),
 'Example Community Health':Facility('Example Community Health','Outreach: Sydney Aged Care, 1 Demonstration Lane, Sydney NSW 2000','02 5550 0143 (fictional)','podiatry@communityhealth.example.invalid',(.40,.24,.22),'community'),
 'Example Pathology Laboratory':Facility('Example Pathology Laboratory','8 Sample Circuit, Sydney NSW 2000','02 5550 0190 (fictional)','reports@examplepathology.example.invalid',(.20,.28,.34),'laboratory'),
}

def normal(value):
    return str(value).replace('μ','µ').replace('₂','2').replace('²','2').replace('’',"'").replace('−','-').encode('cp1252','replace').decode('cp1252')

def width(text,font,size):
    metrics=FONT_METRICS.get(font)
    if metrics:return sum(metrics[1].get(c,500) for c in normal(text))*size/1000
    return sum(.25 if c in ' il.,:' else .8 if c in 'WM' else .5 for c in str(text))*size

def wrap(text,font,size,space):
    lines=[]
    for paragraph in normal(text).split('\n'):
        current=''
        for word in paragraph.split():
            if width(word,font,size)>space:
                # Extremely long identifiers are split safely, never clipped.
                pieces=[];part=''
                for c in word:
                    if width(part+c,font,size)>space:pieces.append(part);part=''
                    part+=c
                if part:pieces.append(part)
            else:pieces=[word]
            for part in pieces:
                if current and width(current+' '+part,font,size)>space:lines.append(current);current=part
                else:current=(current+' '+part).strip()
        lines.append(current)
    return lines

def human(value):
    try:return date.fromisoformat(value[:10]).strftime('%-d %B %Y')
    except (ValueError,TypeError):return 'Not supplied'

class PDF:
    fonts=['Helvetica','Helvetica-Bold','Times-Roman','Times-Bold','Times-Italic','Courier']
    def __init__(self,facility,document):
        self.facility=FACILITIES[facility];self.document=document;self.pages=[];self.commands=[];self.boxes=[];self.y=0
        self.body_font='Times-Roman' if self.facility.layout=='cardiology' else 'Helvetica'
        self.bold_font='Times-Bold' if self.facility.layout=='cardiology' else 'Helvetica-Bold'
        self.size=11 if self.facility.layout=='cardiology' else 10.3
        self.leading=14.7 if self.facility.layout=='cardiology' else 14.2
        self.start()
    def colour(self,colour):return ' '.join(str(v) for v in colour)
    def text(self,x,top,text,size=10,font='Helvetica',colour=(.10,.12,.14),align=None):
        text=normal(text);w=width(text,font,size)
        if align=='right':x-=w
        elif align=='centre':x-=w/2
        escaped=text.replace('\\','\\\\').replace('(','\\(').replace(')','\\)')
        self.commands.append(f'BT /F{self.fonts.index(font)+1} {size} Tf {self.colour(colour)} rg 1 0 0 1 {x:.2f} {842-top:.2f} Tm ({escaped}) Tj ET')
        self.boxes.append((len(self.pages)+1,x,top,w,size,text))
    def rule(self,top,x=43,end=552,colour=(.72,.75,.77),thickness=.5):
        self.commands.append(f'{self.colour(colour)} RG {thickness} w {x} {842-top} m {end} {842-top} l S')
    def rect(self,x,top,w,h,colour):self.commands.append(f'{self.colour(colour)} rg {x} {842-top-h} {w} {h} re f')
    def start(self):
        f=self.facility;d=self.document;self.commands=[]
        if self.pages:
            self.text(43,38,f.name,11,'Helvetica-Bold',f.colour);self.text(552,38,'Jane Doe | '+PATIENT_ID,8,align='right');self.rule(49);self.y=73
            return
        layout=f.layout
        if layout=='cardiology':
            self.text(297.5,48,'HARBOURSIDE',25,'Times-Roman',f.colour,align='centre')
            self.text(297.5,67,'C A R D I O L O G Y',10,'Helvetica',f.colour,align='centre')
            self.text(297.5,88,d['author']+'  |  '+d['role'],10,'Times-Roman',align='centre')
            self.text(297.5,104,f.address,8.4,align='centre');self.text(297.5,118,f.phone+'  |  '+f.email,8.1,align='centre')
            self.rule(130,colour=f.colour);self.y=153
        elif layout=='audiology':
            self.text(43,53,'example',27,'Helvetica',f.colour);self.text(43,75,'COMMUNITY AUDIOLOGY',11,'Helvetica-Bold',f.colour)
            self.text(552,45,f.address,8,align='right');self.text(552,60,f.phone,8,align='right');self.text(552,75,f.email,8,align='right')
            self.rule(93,colour=f.colour,thickness=1.3);self.y=117
        elif layout=='gardens':
            self.text(43,46,'SYDNEY AGED CARE',21,'Times-Roman',f.colour);self.text(43,65,'RESIDENTIAL AGED CARE  /  '+('NURSING SERVICES' if 'nurse' in d.get('role','').lower() else 'ALLIED HEALTH'),8.6,'Helvetica-Bold',f.colour)
            self.text(43,84,f.address,8);self.text(552,84,f.phone,8,align='right');self.text(552,99,f.email,8,align='right')
            self.rule(111);self.rule(111,end=177,colour=f.colour,thickness=2);self.y=135
        elif layout=='community':
            self.rect(43,33,3,56,f.colour);self.text(57,50,'Example Community Health',20,'Helvetica-Bold',f.colour)
            self.text(57,69,'Podiatry & outreach services',10);self.text(57,85,f.phone+'  |  '+f.email,8)
            self.text(43,107,f.address,8);self.rule(119,colour=f.colour);self.y=144
        elif layout=='laboratory':
            self.text(43,42,'EPL',25,'Helvetica-Bold',f.colour);self.text(106,38,'EXAMPLE PATHOLOGY',15,'Helvetica-Bold',f.colour)
            self.text(106,54,'LABORATORY  /  INTEGRATED DIAGNOSTICS',8,'Helvetica',f.colour)
            self.text(552,73,f.address+'  |  '+f.phone,7.7,align='right');self.text(552,86,f.email,7.7,align='right')
            self.rule(98,colour=f.colour,thickness=1);self.y=122
        else:
            self.text(43,45,'Sydney Aged Care',22,'Helvetica-Bold',f.colour)
            self.text(43,65,'GENERAL PRACTICE  /  RESIDENTIAL CARE',8.4,'Helvetica-Bold')
            self.text(43,84,f.address,8);self.text(552,45,f.phone,8,align='right');self.text(552,61,f.email,8,align='right')
            self.rule(96,colour=f.colour);self.y=119
    def finish(self):
        self.rule(785)
        self.text(43,800,FOOTER,8,'Helvetica',(.35,.36,.38))
        page=len(self.pages)+1
        if self.facility.layout=='cardiology':self.text(552,816,f'{page}',9,'Times-Roman',align='right')
        elif self.facility.layout=='audiology':self.text(552,816,f'Audiology | {page}',8,align='right')
        elif self.facility.layout=='laboratory':self.text(552,816,f'Laboratory report | {page}',8,align='right')
        elif self.facility.layout=='community':self.text(43,816,f'Example Community Health  /  Page {page}',7.8)
        else:self.text(552,816,f'Page {page}',8,align='right')
        self.pages.append('\n'.join(self.commands).encode('cp1252'))
    def ensure(self,space):
        if self.y+space>756:self.finish();self.start()
    def paragraph(self,text,size=None,font=None,gap=9):
        font=font or self.body_font;size=size or self.size;lines=wrap(text,font,size,509)
        # Keep short paragraphs together; avoid an orphan last line on a page.
        if len(lines)<=6:self.ensure(len(lines)*self.leading+gap)
        for n,line in enumerate(lines):
            self.ensure(self.leading*(2 if n==len(lines)-2 else 1));self.text(43,self.y,line,size,font);self.y+=self.leading
        self.y+=gap
    def heading(self,text):
        self.ensure(52);self.text(43,self.y,text,11,self.bold_font,self.facility.colour);self.y+=21
    def table(self,headers,rows,widths,size=9):
        def formatted(row):return [wrap(value,'Helvetica',size,w-12) for value,w in zip(row,widths)]
        header_height=25
        def head():
            self.ensure(header_height+24);self.rect(43,self.y-12,509,header_height,(.95,.96,.96));x=43
            for title,w in zip(headers,widths):self.text(x+6,self.y+3,title,size,'Helvetica-Bold');x+=w
            self.y+=header_height;self.rule(self.y-8)
        head()
        for row in rows:
            cells=formatted(row);height=max(len(lines) for lines in cells)*12+(4 if self.facility.layout=='laboratory' else 10)
            if self.y+height>754:self.finish();self.start();head()
            x=43
            for lines,w in zip(cells,widths):
                for n,line in enumerate(lines):self.text(x+6,self.y+n*12,line,size)
                x+=w
            self.y+=height;self.rule(self.y-9,colour=(.86,.87,.88),thickness=.35)
        self.y+=13
    def patient(self,report=False):
        d=self.document;self.text(43,self.y,'Jane Doe',11,'Helvetica-Bold');self.text(305,self.y,'DOB  1 November 1935',9.5);self.y+=17
        self.text(43,self.y,'Patient ID  '+PATIENT_ID,8.6);self.text(305,self.y,'Sydney Aged Care',8.5);self.y+=23
        self.rule(self.y-9);self.text(43,self.y,'Referrer: Dr James Smith  /  Sydney Aged Care' if report else 'Author: '+d['author']+'  /  '+d['role'],9);self.y+=17
        self.text(43,self.y,('Report date: ' if report else 'Document date: ')+human(d.get('document_date')),9)
        self.text(552,self.y,'Collected: '+human(d.get('event_date'))+' (time not supplied)',9,align='right') if report else None
        self.y+=26
    def bytes(self):
        self.finish()
        objects=[b'<< /Type /Catalog /Pages 2 0 R >>',b'']+[f'<< /Type /Font /Subtype /Type1 /BaseFont /{f} /Encoding /WinAnsiEncoding >>'.encode() for f in self.fonts]
        kids=[];resources=' '.join(f'/F{n+1} {n+3} 0 R' for n in range(len(self.fonts)))
        for stream in self.pages:
            number=len(objects)+1;kids.append(f'{number} 0 R')
            objects.extend([f'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << {resources} >> >> /Contents {number+1} 0 R >>'.encode(),b'<< /Length '+str(len(stream)).encode()+b' >>\nstream\n'+stream+b'\nendstream'])
        objects[1]=f'<< /Type /Pages /Kids [{" ".join(kids)}] /Count {len(self.pages)} >>'.encode()
        out=bytearray(b'%PDF-1.4\n');offsets=[]
        for n,obj in enumerate(objects,1):offsets.append(len(out));out+=f'{n} 0 obj\n'.encode()+obj+b'\nendobj\n'
        at=len(out);out+=f'xref\n0 {len(objects)+1}\n0000000000 65535 f \n'.encode()
        for offset in offsets:out+=f'{offset:010d} 00000 n \n'.encode()
        out+=f'trailer\n<< /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{at}\n%%EOF\n'.encode()
        return bytes(out)


def letter(document):
    pdf=PDF(document['organisation'],document)
    pdf.paragraph(human(document['document_date']),10,'Helvetica',gap=8)
    for line in ['Dr James Smith','Sydney Aged Care']:
        pdf.text(43,pdf.y,line,10,pdf.body_font);pdf.y+=14
    pdf.y+=11;pdf.paragraph('Re: Jane Doe | DOB 1 November 1935 | '+PATIENT_ID,10,pdf.bold_font,gap=3)
    pdf.paragraph('Sydney Aged Care',9,'Helvetica',gap=10)
    pdf.paragraph('Dear Dr Smith,',gap=8)
    if document.get('subject'):pdf.paragraph(document['subject'],11,pdf.bold_font,gap=10)
    for block in document['blocks']:
        if block['type']=='paragraph':pdf.paragraph(block['text'])
        elif block['type']=='heading':pdf.heading(block['text'])
        elif block['type']=='table':pdf.table(block['headers'],block['rows'],block['widths'])
        elif block['type']=='plan':
            pdf.heading(block.get('heading','Recommendations'))
            for n,item in enumerate(block['items'],1):pdf.paragraph(f'{n}. {item}',gap=7)
    pdf.ensure(48);pdf.y+=3;pdf.paragraph(document.get('closing','Yours sincerely,'),gap=1)
    pdf.paragraph(document['author'],font=pdf.bold_font,gap=0);pdf.paragraph(document['role'],size=9.5,font='Helvetica',gap=0)
    return pdf.bytes(),pdf.boxes


def pathology(document):
    pdf=PDF('Example Pathology Laboratory',document);pdf.patient(report=True)
    pdf.text(43,pdf.y,document['title'],12,'Helvetica-Bold');pdf.y+=24
    pdf.text(43,pdf.y,'Specimen: '+document['specimen'],9);pdf.text(552,pdf.y,'Accession: '+document['accession'],8.5,'Courier',align='right');pdf.y+=17
    pdf.text(43,pdf.y,'Received by practice: '+human(document.get('received_date')),8.5);pdf.text(552,pdf.y,'Report status: Final',8.5,align='right');pdf.y+=17
    pdf.paragraph('Clinical details: Not supplied on the request.',8.7,'Helvetica',gap=11)
    rows=[]
    for row in document['results']:
        flags=row[4] or ''
        # Keep repeated explanatory source comments out of narrow flag cells.
        label='CRITICAL LOW' if 'CRITICAL' in flags.upper() else 'LOW' if 'LOW' in flags.upper() else 'HIGH' if 'HIGH' in flags.upper() else ''
        rows.append([row[0],row[1],row[2],row[3],label])
    pdf.table(['Test','Result','Units','Reference interval','Flag'],rows,[176,65,75,111,82],size=8.8)
    if document.get('laboratory_flag'):
        pdf.heading('Laboratory comment')
        pdf.paragraph('Critical laboratory flag: sodium 122 mmol/L, below this laboratory\'s critical threshold of 125 mmol/L.',9.5,'Helvetica')
    previous=[row for row in document['results'] if len(row)>5 and row[5]]
    if previous:
        pdf.heading('Previous results — 14 April 2026')
        pdf.paragraph('; '.join(row[0]+' '+row[5]+' '+row[2] for row in previous)+'.',8.6,'Helvetica')
    pdf.paragraph('Authorised for release by the Example Pathology Laboratory duty scientist. Reference intervals are those supplied for this report.',8.5,'Helvetica')
    return pdf.bytes(),pdf.boxes


def clinical_note(document):
    pdf=PDF(document['organisation'],document);pdf.patient()
    pdf.heading(document['title'])
    if document.get('medications'):
        pdf.table(['Medicine','Recorded directions'],document['medications'],[170,339],9.5)
    pdf.paragraph(document.get('display_text') or document['text']);return pdf.bytes(),pdf.boxes


def authored_source(item,organisation):
    """Layout an existing historical source without inventing an encounter."""
    from ..document_titles import title
    d=dict(item,title=title(item),organisation=organisation.replace(' (fictional)',''),document_date=item.get('document_date') or item['date'],event_date=item['date'])
    if item['role'] in ('Cardiologist','Audiologist','Physiotherapist','Podiatrist'):
        d.update(subject=item['title'],blocks=[{'type':'paragraph','text':item['text']}])
        return letter(d)[0]
    return clinical_note(d)[0]
