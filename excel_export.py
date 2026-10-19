"""Project-oriented XLSX report for the web application's export endpoint."""
import io
import zipfile
from datetime import datetime, timezone, timedelta
from xml.sax.saxutils import escape, quoteattr


def column(n):
    result = ''
    while n:
        n, k = divmod(n - 1, 26)
        result = chr(65 + k) + result
    return result


def build_report(data, records, rubrics):
    judges = {j['id']: j for j in data['judges']}
    judge_list = list(judges.values())
    by_project = {p['id']: [r for r in records if r['project_id'] == p['id']] for p in data['projects']}
    generated = datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M')
    sheets = []

    def row(sheet, values, style=0, height=32, merge=False, cells=None):
        sheet['rows'].append((values, style, height, cells or {}))
        r = len(sheet['rows'])
        if merge:
            sheet['merges'].append(f'A{r}:{column(len(sheet["widths"]))}{r}')
        return r

    def mean(done, k=None):
        if not done:
            return None
        return round(sum(r['total'] if k is None else r['values'][str(k)] for r in done) / len(done), 2)

    for category in rubrics:
        dims = category['dimensions']
        projects = [p for p in data['projects'] if p['category'] == category['id']]
        averages = {p['id']: mean([r for r in by_project[p['id']] if r['complete']]) for p in projects}
        # Active scored projects rank first, unscored next, archived last.
        projects.sort(key=lambda p: (not p['active'], averages[p['id']] is None, -(averages[p['id']] or 0), p['number']))
        ranks = {}
        previous = None
        ranked_count = 0
        for p in projects:
            avg = averages[p['id']]
            if not p['active'] or avg is None:
                ranks[p['id']] = None
                continue
            ranked_count += 1
            rank = ranks[previous] if previous is not None and averages[previous] == avg else ranked_count
            ranks[p['id']] = rank
            previous = p['id']
        summary_cols = 7 + len(judge_list)
        detail_cols = len(dims) + 4
        width_count = max(summary_cols, detail_cols)
        widths = ([18, 24, 36, 24, 24] + [20] * (width_count - 5))
        sheet = {'name':category['name'], 'widths':widths, 'rows':[], 'merges':[], 'breaks':[], 'hyperlinks':[]}
        sheets.append(sheet)
        row(sheet, [category['name'] + ' · 评分汇总及排名'], 1, 42, True)
        row(sheet, ['按项目平均总分降序，同分并列；未全部评完的排名为暂定。未评分不作零分，停用项目不参与排名。'], 2, 32, True)
        row(sheet, ['排名','项目序号','项目主题（点击查看分项）','参赛人','部门'] + [j['name']+'\n总分' for j in judge_list] + ['项目平均总分','完整评委数'], 3, 54)
        summary_refs = {}
        for index,p in enumerate(projects):
            rr = by_project[p['id']]
            done = [r for r in rr if r['complete']]
            totals = {r['judge_id']:r['total'] for r in rr}
            rank = ranks[p['id']]
            status = '停用' if not p['active'] else '未评分' if averages[p['id']] is None else rank
            summary_refs[p['id']] = row(sheet, [status,p['number'],p['title'],p['person'],p['department']] + [totals.get(j['id']) for j in judge_list] + [averages[p['id']],len(done)], 4 if index%2==0 else 5, 48, cells={3:14, summary_cols-1:8})
        if not projects:
            row(sheet, ['本分类暂无项目'], 2, 36, True)
        row(sheet, [], height=26)
        row(sheet, ['各项目分项评分 · 以下按排名顺序排列'], 6, 36, True)
        pending_link = None
        for p in projects:
            row(sheet, [], height=22)
            rank = ranks[p['id']]
            prefix = '第 '+str(rank)+' 名' if rank is not None else '未参与排名'
            detail_start = row(sheet, [f"{prefix}  |  项目 {p['number']:02d}  |  {p['title']}"], 6, 42, True)
            if pending_link is not None:
                sheet['hyperlinks'].append((pending_link, "'"+sheet['name']+"'!A"+str(detail_start)))
            pending_link = f'C{summary_refs[p["id"]]}'
            row(sheet, [f"参赛人：{p['person']}    部门：{p['department']}    项目平均总分：{averages[p['id']] if averages[p['id']] is not None else '未评分'}"], 7, 36, True)
            row(sheet, ['评委']+[f"{d['name']}\n（满分 {d['max']} 分）" for d in dims]+['已填项小计','总分（100分）','评分状态'], 3, 58)
            rr = by_project[p['id']]
            for index,r in enumerate(rr):
                j = judges[r['judge_id']]
                state = '已完成' if r['complete'] else '评分中' if r['values'] else '未评分'
                row(sheet, [j['name']+('（停用）' if not j['active'] else '')]+[r['values'].get(str(k)) for k in range(len(dims))]+[r['subtotal'] if r['values'] else None,r['total'],state], 4 if index%2==0 else 5, 38, cells={detail_cols-1:8, detail_cols:11 if r['complete'] else 12})
            done = [r for r in rr if r['complete']]
            detail_end = row(sheet, ['项目平均分']+[mean(done,k) for k in range(len(dims))]+[None,mean(done),f'{len(done)} 位完整评分'], 9, 42, cells={k:10 for k in range(2,detail_cols)})
            back = row(sheet, ['↑ 返回本模块排名'], 13, 28, True)
            sheet['hyperlinks'].append((f'A{back}',"'"+sheet['name']+"'!A1"))
            row(sheet, [], height=16)
        row(sheet, [], height=18)
        rules_start = row(sheet, ['本模块评分细则 · 对照原始评分表'], 6, 34, True)
        if pending_link is not None:
            sheet['hyperlinks'].append((pending_link, "'"+sheet['name']+"'!A"+str(rules_start)))
        for d in dims:
            r = row(sheet, [f"{d['name']}\n满分 {d['max']} 分", d['description']], 7, 62)
            sheet['merges'].append(f'B{r}:{column(width_count)}{r}')
        row(sheet, ['导出时间：'+generated+'    平均分仅计完整评分，含停用评委的完整历史评分；分数为当前数据快照。'], 2, 32, True)
    return write_xlsx(sheets)


def write_xlsx(sheets):
    ns = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    relns = 'http://schemas.openxmlformats.org/package/2006/relationships'
    offrel = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
    # Styles: plain, title, note, header, white row, stripe, project title,
    # metadata, numeric total, average label, average number, complete, pending.
    fonts = [
        '<font><sz val="11"/><color rgb="FF243B53"/><name val="Microsoft YaHei"/></font>',
        '<font><b/><sz val="18"/><color rgb="FFFFFFFF"/><name val="Microsoft YaHei"/></font>',
        '<font><sz val="10"/><color rgb="FF73839A"/><name val="Microsoft YaHei"/></font>',
        '<font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Microsoft YaHei"/></font>',
        '<font><b/><sz val="12"/><color rgb="FF243B53"/><name val="Microsoft YaHei"/></font>',
        '<font><b/><sz val="11"/><color rgb="FF23704E"/><name val="Microsoft YaHei"/></font>',
        '<font><sz val="10"/><color rgb="FF9B773B"/><name val="Microsoft YaHei"/></font>',
        '<font><u/><sz val="11"/><color rgb="FF2868B0"/><name val="Microsoft YaHei"/></font>',
    ]
    fills = ['<fill><patternFill patternType="none"/></fill>', '<fill><patternFill patternType="gray125"/></fill>']
    for color in ['172D4D', '31516D', 'FFFFFF', 'F4F7FB', 'E4EDF9', 'F0F4FA', 'E4F3EA']:
        fills.append(f'<fill><patternFill patternType="solid"><fgColor rgb="FF{color}"/><bgColor indexed="64"/></patternFill></fill>')
    def xf(font=0, fill=0, border=0, number=0, align='left'):
        return f'<xf fontId="{font}" fillId="{fill}" borderId="{border}" numFmtId="{number}" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyNumberFormat="1" applyAlignment="1"><alignment horizontal="{align}" vertical="center" wrapText="1" indent="{1 if align=="left" else 0}"/></xf>'
    styles = [xf(), xf(1,2), xf(2), xf(3,3,1,align='center'), xf(0,4,1,0,'center'), xf(0,5,1,0,'center'), xf(4,6), xf(0,7), xf(4,5,1,2,'center'), xf(5,8,1,align='center'), xf(5,8,1,2,'center'), xf(5,4,1,align='center'), xf(6,5,1,align='center'), xf(7), xf(7,4,1)]
    xmlstyles = f'<styleSheet xmlns="{ns}"><numFmts count="1"><numFmt numFmtId="164" formatCode="0.##"/></numFmts><fonts count="{len(fonts)}">'+''.join(fonts)+f'</fonts><fills count="{len(fills)}">'+''.join(fills)+'</fills><borders count="2"><border/><border><left style="thin"><color rgb="FFE2E8F0"/></left><right style="thin"><color rgb="FFE2E8F0"/></right><top style="thin"><color rgb="FFE2E8F0"/></top><bottom style="thin"><color rgb="FFE2E8F0"/></bottom></border></borders><cellStyleXfs count="1"><xf/></cellStyleXfs><cellXfs count="'+str(len(styles))+'">'+''.join(styles)+'</cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>'
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml','<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'+''.join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' for i in range(1,len(sheets)+1))+'<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>')
        z.writestr('_rels/.rels',f'<Relationships xmlns="{relns}"><Relationship Id="rId1" Type="{offrel}officeDocument" Target="xl/workbook.xml"/></Relationships>')
        z.writestr('xl/workbook.xml',f'<workbook xmlns="{ns}" xmlns:r="{offrel[:-1]}"><sheets>'+''.join(f'<sheet name={quoteattr(s["name"])} sheetId="{i}" r:id="rId{i}"/>' for i,s in enumerate(sheets,1))+'</sheets></workbook>')
        z.writestr('xl/_rels/workbook.xml.rels',f'<Relationships xmlns="{relns}">'+''.join(f'<Relationship Id="rId{i}" Type="{offrel}worksheet" Target="worksheets/sheet{i}.xml"/>' for i in range(1,len(sheets)+1))+f'<Relationship Id="rId{len(sheets)+1}" Type="{offrel}styles" Target="styles.xml"/></Relationships>')
        z.writestr('xl/styles.xml',xmlstyles)
        for i,s in enumerate(sheets,1):
            last = column(len(s['widths']))
            xml = f'<worksheet xmlns="{ns}"><dimension ref="A1:{last}{len(s["rows"])}"/><sheetViews><sheetView workbookViewId="0" showGridLines="0"></sheetView></sheetViews><cols>'
            xml += ''.join(f'<col min="{k}" max="{k}" width="{w}" customWidth="1"/>' for k,w in enumerate(s['widths'],1))+'</cols><sheetData>'
            for ri,(values,style,height,cellstyles) in enumerate(s['rows'],1):
                xml += f'<row r="{ri}" ht="{height}" customHeight="1">'
                for ci in range(1,len(s['widths'])+1):
                    value = values[ci-1] if ci <= len(values) else None
                    st = cellstyles.get(ci,style)
                    ref = f'{column(ci)}{ri}'
                    if value is None:
                        xml += f'<c r="{ref}" s="{st}"/>'
                    elif isinstance(value,(int,float)):
                        xml += f'<c r="{ref}" s="{st}"><v>{value}</v></c>'
                    else:
                        text = ''.join(ch for ch in str(value) if ch in '\n\r\t' or 32<=ord(ch)<=0xD7FF or 0xE000<=ord(ch)<=0xFFFD or 0x10000<=ord(ch)<=0x10FFFF)
                        xml += f'<c r="{ref}" s="{st}" t="inlineStr"><is><t xml:space="preserve">{escape(text)}</t></is></c>'
                xml += '</row>'
            xml += '</sheetData>'
            if s['merges']:
                xml += f'<mergeCells count="{len(s["merges"])}">'+''.join(f'<mergeCell ref="{r}"/>' for r in s['merges'])+'</mergeCells>'
            if s['hyperlinks']:
                xml += '<hyperlinks>'+''.join(f'<hyperlink ref="{ref}" location={quoteattr(location)}/>' for ref,location in s['hyperlinks'])+'</hyperlinks>'
            xml += '<printOptions horizontalCentered="1"/><pageMargins left="0.25" right="0.25" top="0.4" bottom="0.4" header="0.2" footer="0.2"/><pageSetup paperSize="9" orientation="landscape" fitToWidth="1" fitToHeight="0"/>'
            if s['breaks']:
                xml += f'<rowBreaks count="{len(s["breaks"])}" manualBreakCount="{len(s["breaks"])}">'+''.join(f'<brk id="{r}" max="16383" man="1"/>' for r in s['breaks'])+'</rowBreaks>'
            xml += '</worksheet>'
            z.writestr(f'xl/worksheets/sheet{i}.xml',xml)
    return out.getvalue()
