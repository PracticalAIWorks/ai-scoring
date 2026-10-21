"""Validate configurable scoring rules before storing them with a competition."""
import json
import re


def text(value,label,limit=200,optional=False):
    if not isinstance(value,str) or len(value)>limit or (not optional and not value.strip()):raise ValueError(label+'不能为空或内容过长')
    return value.strip()


def validate(raw):
    if not isinstance(raw,list) or not 1<=len(raw)<=20:raise ValueError('请配置1～20个项目分类')
    result=[];ids=set();names=set()
    for category in raw:
        if not isinstance(category,dict):raise ValueError('分类格式无效')
        cid=category.get('id','');name=text(category.get('name'),'分类名称',30)
        if not isinstance(cid,str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,39}',cid) or cid in ids or name in names:raise ValueError('分类编号或名称重复、无效')
        ids.add(cid);names.add(name);dimensions=category.get('dimensions')
        if not isinstance(dimensions,list) or not 1<=len(dimensions)<=20:raise ValueError('每个分类需配置1～20个评分项')
        dims=[]
        for dimension in dimensions:
            if not isinstance(dimension,dict):raise ValueError('评分项格式无效')
            maximum=dimension.get('max');subitems=dimension.get('subitems')
            if type(maximum) is not int or not 1<=maximum<=1000:raise ValueError('评分项满分应为1～1000的整数')
            if not isinstance(subitems,list) or not 1<=len(subitems)<=20:raise ValueError('每个评分项需配置1～20个子项')
            subs=[]
            for sub in subitems:
                if not isinstance(sub,dict) or type(sub.get('max')) is not int or not 1<=sub['max']<=1000:raise ValueError('子项满分应为正整数')
                criteria=sub.get('elements')
                if not isinstance(criteria,list) or len(criteria)!=5:raise ValueError('每个子项请填写5条核对要素')
                subs.append({'name':text(sub.get('name'),'子项名称',80),'max':sub['max'],
                    'definition':text(sub.get('definition',''),'子项定义',1500,True),
                    'elements':[text(c,'核对要素',500) for c in criteria],
                    'scoring_rule':text(sub.get('scoring_rule','按核对要素与展示情况综合判断，使用整数滑块或输入给分。'),'计分参考',1500,True)})
            if sum(s['max'] for s in subs)!=maximum:raise ValueError('子项满分之和必须等于所属评分项满分')
            bands=dimension.get('bands',[])
            if not isinstance(bands,list) or len(bands)>10:raise ValueError('评分区间格式无效')
            cleaned=[];next_min=0
            for band in bands:
                if not isinstance(band,dict) or type(band.get('min')) is not int or type(band.get('max')) is not int or band['min']!=next_min or not band['min']<=band['max']<=maximum:raise ValueError('评分区间应从0连续覆盖至满分')
                cleaned.append({'min':band['min'],'max':band['max'],'label':text(band.get('label'),'档位名称',50),'description':text(band.get('description',''),'档位说明',1000,True)})
                next_min=band['max']+1
            if bands and next_min!=maximum+1:raise ValueError('评分区间应覆盖至满分')
            dims.append({'name':text(dimension.get('name'),'评分项名称',80),'max':maximum,'description':text(dimension.get('description',''),'评分项说明',1500,True),'bands':cleaned,'subitems':subs})
        if sum(d['max'] for d in dims)>1000:raise ValueError('分类总分不能超过1000分')
        result.append({'id':cid,'name':name,'final_enabled':bool(category.get('final_enabled',True)),'dimensions':dims})
    # Bound size for settings snapshots and browser rendering.
    if len(json.dumps(result,ensure_ascii=False))>500000:raise ValueError('评分规则内容过多')
    return result


def structure(rules):
    return [(c['id'],[(d['max'],[s['max'] for s in d['subitems']]) for d in c['dimensions']]) for c in rules]
