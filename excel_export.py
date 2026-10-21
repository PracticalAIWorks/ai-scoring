"""Project-oriented XLSX report for the web application's export endpoint."""
import io
import re
import zipfile
from datetime import datetime, timezone, timedelta
from decimal import Decimal, ROUND_HALF_UP
from xml.sax.saxutils import escape, quoteattr


def column(n):
    result = ''
    while n:
        n, k = divmod(n - 1, 26)
        result = chr(65 + k) + result
    return result


def build_report(data, records, rubrics):
    is_final=data.get('round')=='final'
    combined=data.get('combined_scores',{})
    initial={r['project_id']:r['preliminary_score'] for r in data.get('finalists',[])}
    initial.update({pid:r['preliminary_score'] for pid,r in combined.items() if 'preliminary_score' in r})
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
        total=sum((Decimal(str(r['total'] if k is None else r['values'][str(k)])) for r in done),Decimal(0))
        return float((total/len(done)).quantize(Decimal('0.01'),rounding=ROUND_HALF_UP))

    for category in rubrics:
        dims = category['dimensions']
        projects = [p for p in data['projects'] if p['category'] == category['id']]
        averages = {p['id']: combined.get(p['id'],{}).get('score') if is_final else mean([r for r in by_project[p['id']] if r['complete'] and judges[r['judge_id']]['active']]) for p in projects}
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
        summary_cols = (9 if is_final else 7) + len(judge_list)
        detail_cols = len(dims) + 4
        width_count = max(summary_cols, detail_cols)
        widths = ([18, 24, 36, 24, 24] + [20] * (width_count - 5))
        base=re.sub(r'[\\/*?:\[\]]','_',category['name']).strip("'") or '分类'
        sheet_name=base[:31];suffix=1
        while sheet_name.casefold() in {s['name'].casefold() for s in sheets}:
            suffix+=1;sheet_name=base[:26]+'_'+str(suffix)
        sheet = {'name':sheet_name, 'widths':widths, 'rows':[], 'merges':[], 'breaks':[], 'hyperlinks':[]}
        sheets.append(sheet)
        row(sheet, [category['name'] + (' · 复试综合成绩及排名' if is_final else ' · 评分汇总及排名')], 1, 42, True)
        row(sheet, ['按综合最终分数降序，同分并列；复试未完整评分不计入平均，取消入围及停用项目不排名。停用评委仅保留明细，不计平均。' if is_final else '按项目平均总分降序，同分并列；未全部评完的排名为暂定。未评分不作零分，停用项目不参与排名。停用评委仅保留明细，不计平均。'], 2, 32, True)
        row(sheet, ['排名','项目序号','项目主题（点击查看分项）','参赛人','部门'] + [j['name']+('（停用，不计均分）' if not j['active'] else '')+'\n'+('复试总分' if is_final else '总分') for j in judge_list] + (['初试平均分','复试平均分','最终分数','完整评委数'] if is_final else ['项目平均总分','完整评委数']), 3, 54)
        summary_refs = {}
        for index,p in enumerate(projects):
            rr = by_project[p['id']]
            done = [r for r in rr if r['complete'] and judges[r['judge_id']]['active']]
            totals = {r['judge_id']:r['total'] for r in rr}
            rank = ranks[p['id']]
            status = '停用' if not p['active'] else '未评分' if averages[p['id']] is None else rank
            final_average=combined.get(p['id'],{}).get('final_average',mean(done))
            summary_refs[p['id']] = row(sheet, [status,p['number'],p['title'],p['person'],p['department']] + [totals.get(j['id']) for j in judge_list] + ([initial.get(p['id']),final_average,averages[p['id']],len(done)] if is_final else [averages[p['id']],len(done)]), 4 if index%2==0 else 5, 48, cells={3:14, summary_cols-1:8})
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
            row(sheet, [f"参赛人：{p['person']}    部门：{p['department']}    {'综合最终分数' if is_final else '项目平均总分'}：{averages[p['id']] if averages[p['id']] is not None else '未评分'}"], 7, 36, True)
            row(sheet, ['评委']+[f"{d['name']}\n（满分 {d['max']} 分）" for d in dims]+['已填项小计',f'总分（{sum(d["max"] for d in dims)}分）','评分状态'], 3, 58)
            rr = by_project[p['id']]
            for index,r in enumerate(rr):
                j = judges[r['judge_id']]
                state = '已完成' if r['complete'] else '评分中' if r['values'] else '未评分'
                row(sheet, [j['name']+('（停用）' if not j['active'] else '')]+[r['values'].get(str(k)) for k in range(len(dims))]+[r['subtotal'] if r['values'] else None,r['total'],state], 4 if index%2==0 else 5, 38, cells={detail_cols-1:8, detail_cols:11 if r['complete'] else 12})
            done = [r for r in rr if r['complete'] and judges[r['judge_id']]['active']]
            detail_end = row(sheet, ['复试平均分' if is_final else '项目平均分']+[mean(done,k) for k in range(len(dims))]+[None,mean(done),f'{len(done)} 位完整评分'], 9, 42, cells={k:10 for k in range(2,detail_cols)})
            for k,d in enumerate(dims):
                subitems=d.get('subitems',[])
                if not subitems: continue
                row(sheet,[f"{d['name']} · 子项评分明细（{d['max']}分）"],6,34,True)
                row(sheet,['评委']+[f"{sub['name']}\n（满分{sub['max']}分）" for sub in subitems]+['该项汇总','明细状态'],3,58)
                for index,r in enumerate(rr):
                    subvalues=r.get('subvalues',{}).get(str(k),{})
                    state='已完成' if len(subvalues)==len(subitems) else '未填完整' if subvalues else '历史评分未细分' if str(k) in r['values'] else '未评分'
                    row(sheet,[judges[r['judge_id']]['name']+('（停用）' if not judges[r['judge_id']]['active'] else '')]+[subvalues.get(str(si)) for si in range(len(subitems))]+[r['values'].get(str(k)),state],4 if index%2==0 else 5,38)
                known=[r for r in done if len(r.get('subvalues',{}).get(str(k),{}))==len(subitems)]
                if known:
                    subaverages=[round(sum(r['subvalues'][str(k)][str(si)] for r in known)/len(known),2) for si in range(len(subitems))]
                    row(sheet,['子项平均分']+subaverages+[round(sum(r['values'][str(k)] for r in known)/len(known),2),f'{len(known)}位有完整明细'],9,42)
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
            for band in d.get('bands',[]):
                r = row(sheet, [f"{band['min']}～{band['max']} 分 · {band['label']}",band['description']], 7, 52)
                sheet['merges'].append(f'B{r}:{column(width_count)}{r}')
            for sub in d.get('subitems',[]):
                r=row(sheet,[f"{sub['name']}\n满分{sub['max']}分",sub['definition']+'\n'+sub['scoring_rule']],7,66)
                sheet['merges'].append(f'B{r}:{column(width_count)}{r}')
                for ei,element in enumerate(sub['elements'],1):
                    r=row(sheet,[f'核对要素 {ei}',element],7,38)
                    sheet['merges'].append(f'B{r}:{column(width_count)}{r}')
        row(sheet, ['导出时间：'+generated+'    平均分仅计完整评分，含停用评委的完整历史评分；分数为当前数据快照。'], 2, 32, True)
    return write_xlsx(sheets)


def build_ranking_report(board,settings):
    sheets=[]
    for group in board['categories']:
        base=re.sub(r'[\\/*?:\[\]]','_',group['name']).strip("'") or '分类'
        name=base[:31];suffix=1
        while name.casefold() in {sheet['name'].casefold() for sheet in sheets}:
            suffix+=1;name=base[:26]+'_'+str(suffix)
        rows=[([board['title']+' · '+group['name']],1,42,{}),
            ([board['score_caption']+f"；初试占比{settings['preliminary_weight']}%，复试占比{settings['final_weight']}%；导出为当前数据快照。"],2,36,{}),
            (['排名','项目主题','姓名','部门','初试成绩','初试来源','复试成绩','复试来源','最终分数','最终来源'],3,44,{})]
        for i,p in enumerate(group['projects']):
            manual=p['result_source']=='manual' or settings['mode']=='manual'
            rows.append(([p['rank'] or '待评分',p['title'],p['person'],p['department'],p['preliminary_score'] if p['preliminary_score'] is not None else '待评分','手动录入' if p['initial_source']=='manual' else '评委平均分',p['final_average'] if p['final_average'] is not None else '待评分','手动录入' if p['final_source']=='manual' else '评委平均分',p['score'] if p['score'] is not None else '待评分','手动最终分' if manual else board['score_caption']],4+i%2,44,{}))
        sheets.append({'name':name,'widths':[10,48,18,24,16,18,16,18,16,20],'rows':rows,'merges':['A1:J1','A2:J2'],'breaks':[],'hyperlinks':[]})
    if not sheets:sheets=[{'name':'排行榜','widths':[60],'rows':[(['暂无开放的排行榜模块'],2,42,{})],'merges':[],'breaks':[],'hyperlinks':[]}]
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
