"""Bounded, module-specific XLSX/CSV project import without runtime dependencies."""
import csv
import io
import posixpath
import re
import zipfile
from decimal import Decimal, InvalidOperation
from xml.etree import ElementTree as ET

NS='http://schemas.openxmlformats.org/spreadsheetml/2006/main'
REL='http://schemas.openxmlformats.org/officeDocument/2006/relationships'
CATEGORY_NAMES={'智能体设计':'agent','智能体搭建':'agent','解决方案设计':'solution','解决方案':'solution','提示词设计':'prompt'}
ALIASES={
    'number':{'序号','项目序号','上台顺序','宣讲顺序','编号'},
    'title':{'项目主题','主题','设计主题','项目名称','方案名称','参赛主题'},
    'person':{'姓名','参赛人','参赛人姓名','申报人','报名人姓名'},
    'department':{'部门','申报部门','所属部门','部门名称'},
    'category':{'分类','项目分类','参赛类别','所属模块','模块'},
}


class ProjectImportError(ValueError):pass


def _xml(archive,name):
    try:raw=archive.read(name)
    except KeyError:raise ProjectImportError('Excel文件缺少必要内容，请另存为xlsx后重试')
    if len(raw)>6*1024*1024 or b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():raise ProjectImportError('Excel文件内容过大或格式不受支持')
    try:return ET.fromstring(raw)
    except ET.ParseError:raise ProjectImportError('Excel内容无法读取，请另存为xlsx后重试')


def _xlsx_rows(raw,category,names):
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            members=archive.infolist()
            if len(members)>200 or len({m.filename for m in members})!=len(members) or sum(m.file_size for m in members)>12*1024*1024:raise ProjectImportError('Excel内容过大，请只保留需要导入的项目表')
            workbook=_xml(archive,'xl/workbook.xml');sheets=workbook.findall(f'{{{NS}}}sheets/{{{NS}}}sheet')
            matching=[s for s in sheets if names.get(s.get('name',''))==category]
            if len(matching)==1:sheet=matching[0]
            elif len(sheets)==1:
                sheet=sheets[0]
                if names.get(sheet.get('name',''),category)!=category:raise ProjectImportError('表格所属模块与当前模块不同，请切换模块后导入')
            else:raise ProjectImportError('请只保留当前模块一个工作表，或将工作表命名为对应模块名称')
            relationships=_xml(archive,'xl/_rels/workbook.xml.rels')
            link=next((r for r in relationships if r.get('Id')==sheet.get(f'{{{REL}}}id')),None)
            if link is None or link.get('TargetMode')=='External':raise ProjectImportError('Excel工作表关联无效')
            target=link.get('Target','');name=posixpath.normpath(target.lstrip('/') if target.startswith('/') else posixpath.join('xl',target))
            if not name.startswith('xl/worksheets/') or not name.endswith('.xml'):raise ProjectImportError('Excel工作表位置无效')
            shared=[]
            if 'xl/sharedStrings.xml' in archive.namelist():
                shared=[''.join(si.itertext()) for si in _xml(archive,'xl/sharedStrings.xml').findall(f'{{{NS}}}si')]
            content=_xml(archive,name);rows=[]
            for index,row in enumerate(content.findall(f'{{{NS}}}sheetData/{{{NS}}}row'),1):
                if index>10000:raise ProjectImportError('表格行数过多，请移除多余的空白行及格式')
                values={};formulas=set()
                for cell in row.findall(f'{{{NS}}}c'):
                    address=cell.get('r','');match=re.match(r'^([A-Z]+)\d+$',address)
                    if not match:continue
                    column=0
                    for char in match[1]:column=column*26+ord(char)-64
                    if column>50:continue
                    value=cell.findtext(f'{{{NS}}}v','')
                    if cell.get('t')=='s':
                        try:value=shared[int(value)]
                        except (ValueError,IndexError):raise ProjectImportError('Excel文本索引无效')
                    elif cell.get('t')=='inlineStr':value=''.join(cell.find(f'{{{NS}}}is').itertext()) if cell.find(f'{{{NS}}}is') is not None else ''
                    if cell.find(f'{{{NS}}}f') is not None:formulas.add(column-1)
                    values[column-1]=value
                if not any(str(v).strip() for v in values.values()) and not formulas:continue
                rows.append((int(row.get('r',index)),[values.get(i,'') for i in range(max(values,default=0)+1)],formulas))
            return rows
    except (zipfile.BadZipFile,RuntimeError,EOFError,ValueError,KeyError) as error:
        if isinstance(error,ProjectImportError):raise
        raise ProjectImportError('Excel文件无法读取，请另存为xlsx后重试')


def parse_projects(raw,filename,category,category_names=None):
    names={**CATEGORY_NAMES,**(category_names or {})}
    if not raw or len(raw)>4*1024*1024:raise ProjectImportError('请使用4MB以内的Excel或CSV文件')
    if filename.lower().endswith('.xlsx'):source=_xlsx_rows(raw,category,names)
    elif filename.lower().endswith('.csv'):
        try:text=raw.decode('utf-8-sig')
        except UnicodeDecodeError:
            try:text=raw.decode('gb18030')
            except UnicodeDecodeError:raise ProjectImportError('CSV编码无法读取，请保存为UTF-8 CSV')
        if '\x00' in text:raise ProjectImportError('请使用Excel导出的CSV文本文件')
        try:
            try:dialect=csv.Sniffer().sniff(text[:4096],delimiters=',;\t')
            except csv.Error:dialect=csv.excel
            source=[]
            for i,row in enumerate(csv.reader(io.StringIO(text),dialect),1):
                if i>10000:raise ProjectImportError('表格行数过多，请只保留需要导入的项目')
                if any(c.strip() for c in row):source.append((i,row,set()))
        except csv.Error:raise ProjectImportError('CSV格式无法读取')
    else:raise ProjectImportError('支持xlsx和CSV文件；旧版xls请先另存为xlsx')
    mapping=None;header_index=None
    for index,(_,row,_) in enumerate(source[:20]):
        found={}
        for col,label in enumerate(row):
            label=re.sub(r'\s|\*|（.*?）|\(.*?\)','',str(label))
            for key,aliases in ALIASES.items():
                if label in aliases:found[key]=col
        if all(key in found for key in ('number','title','person','department')):mapping=found;header_index=index;break
    if mapping is None:raise ProjectImportError('未找到表头，请使用：序号、项目主题、姓名、部门')
    rows=[]
    for row_number,values,formulas in source[header_index+1:]:
        selected={key:str(values[col]).strip() if col<len(values) else '' for key,col in mapping.items()}
        if not any(selected.get(key) for key in ('number','title','person','department')):continue
        if any(col in formulas for key,col in mapping.items()):raise ProjectImportError(f'第{row_number}行包含公式，请粘贴为值后导入')
        if selected.get('category') and names.get(selected['category'],selected['category'])!=category:raise ProjectImportError(f'第{row_number}行属于其他模块，请分模块导入')
        rows.append({**{key:selected[key] for key in ('number','title','person','department')},'source_row':row_number})
        if len(rows)>500:raise ProjectImportError('每次最多导入500个项目')
    return rows


def validate_projects(db,category,rows):
    if not isinstance(rows,list) or not 1<=len(rows)<=500:raise ProjectImportError('请填写1～500个项目后导入')
    existing=db.execute('SELECT number,title,person,department FROM projects WHERE category=?',(category,)).fetchall()
    numbers={r['number'] for r in existing};identities={(r['title'],r['person'],r['department']) for r in existing};clean=[]
    for index,row in enumerate(rows,1):
        if not isinstance(row,dict):raise ProjectImportError('项目数据格式错误')
        line=row.get('source_row',index)
        if type(line) is not int or not 1<=line<=1048576:line=index
        try:
            if isinstance(row.get('number'),bool):raise ValueError()
            number=Decimal(str(row.get('number','')))
            if not number.is_finite() or number!=number.to_integral_value() or not 1<=number<=99999:raise ValueError()
            number=int(number)
        except (ValueError,InvalidOperation):raise ProjectImportError(f'第{line}行：序号应为1～99999的整数')
        item={'number':number,'source_row':line}
        for key,label,limit in [('title','项目主题',200),('person','姓名',100),('department','部门',100)]:
            value=row.get(key)
            if not isinstance(value,str) or not value.strip() or len(value.strip())>limit:raise ProjectImportError(f'第{line}行：{label}不能为空，最多{limit}字')
            item[key]=value.strip()
        if number in numbers:raise ProjectImportError(f'第{line}行：当前模块序号{number}已存在或重复，请修改序号；已有项目请直接编辑')
        identity=(item['title'],item['person'],item['department'])
        if identity in identities:raise ProjectImportError(f'第{line}行：相同主题、姓名及部门的项目已存在或重复')
        numbers.add(number);identities.add(identity);clean.append(item)
    return clean
