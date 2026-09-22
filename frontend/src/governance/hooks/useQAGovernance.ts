import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError } from "../../api/client";
import {
  addQAAlternative,
  addQANegative,
  batchExpireQA,
  batchRestoreQA,
  batchReviewQA,
  createQA,
  deleteQAAlternative,
  deleteQANegative,
  expireQA,
  exportQA,
  fetchQAList,
  importQA,
  patchQA,
  restoreQA,
  reviewQA,
} from "../api/governanceApi";
import {
  projectGovernanceError,
  type DatasetRevisionRequest,
  type DatasetStatus,
  type GovernanceErrorView,
  type GovernanceScope,
  type QAAlternativeCreate,
  type QABatchResult,
  type QABatchReviewItem,
  type QABatchRevisionItem,
  type QACreate,
  type QAExportFormat,
  type QAImportRequest,
  type QAImportResult,
  type QAKnowledge,
  type QAListFilters,
  type QANegativeCreate,
  type QAReviewRequest,
  type QAUpdate,
} from "../model/governanceModel";
import type { GovernanceLoadStatus } from "./useDatasetGovernance";
import { useStableGovernanceScope } from "./useStableGovernanceScope";
const PAGE_SIZE=10; const aborted=(e:unknown)=>e instanceof ApiError&&e.kind==="aborted";

export function useQAGovernance(inputScope: GovernanceScope|null, online:boolean|null, datasetStatus:DatasetStatus|null="active", deepLinkQaId:string|null=null){
 const {scope,key}=useStableGovernanceScope(inputScope); const readable=datasetStatus==="active"||datasetStatus==="archived"; const writable=datasetStatus==="active";
 const [status,setStatus]=useState<GovernanceLoadStatus>(!scope?"scope":"loading"); const [items,setItems]=useState<QAKnowledge[]>([]); const [itemsScopeKey,setItemsScopeKey]=useState(""); const [filters,setFilterState]=useState<QAListFilters>({}); const [page,setPageState]=useState(1); const [error,setError]=useState<GovernanceErrorView|null>(null); const [mutatingId,setMutatingId]=useState<string|null>(null); const [truncated,setTruncated]=useState(false); const [focusedQaId,setFocusedQaId]=useState<string|null>(null); const [deepLinkNotice,setDeepLinkNotice]=useState<string|null>(null);
 const keyRef=useRef(key); const epochRef=useRef(0); if(keyRef.current!==key){keyRef.current=key;epochRef.current+=1;} const loadRef=useRef<AbortController|null>(null); const mutationRef=useRef<AbortController|null>(null); const loadSeq=useRef(0); const busyRef=useRef(false); const deepLinkAppliedRef=useRef("");
 const current=useCallback((k:string,e:number)=>keyRef.current===k&&epochRef.current===e,[]); const requestFilters=useMemo(()=>({...filters,limit:500}),[filters]);
 const load=useCallback(async(preserveError=false)=>{const s=scope,k=key,e=epochRef.current;if(!s||!readable||online!==true){setItems([]);setItemsScopeKey("");setTruncated(false);setStatus(!s?"scope":online===false?"offline":"ready");return false;}loadRef.current?.abort();const c=new AbortController();loadRef.current=c;const seq=++loadSeq.current;setStatus("loading");if(!preserveError)setError(null);try{const r=await fetchQAList(s,requestFilters,{signal:c.signal});if(c.signal.aborted||seq!==loadSeq.current||!current(k,e))return false;setItems(r.items);setItemsScopeKey(k);setTruncated(r.count>=500);setStatus("ready");return true;}catch(x){if(c.signal.aborted||!current(k,e)||aborted(x))return false;setItems([]);setItemsScopeKey("");setTruncated(false);setStatus("error");if(!preserveError)setError(projectGovernanceError(x));return false;}},[current,key,online,readable,requestFilters,scope]);
 useEffect(()=>{loadRef.current?.abort();mutationRef.current?.abort();busyRef.current=false;setMutatingId(null);setItems([]);setItemsScopeKey("");setTruncated(false);setPageState(1);setError(null);deepLinkAppliedRef.current="";setFocusedQaId(null);setDeepLinkNotice(null);void load();return()=>{loadRef.current?.abort();mutationRef.current?.abort();};},[key,online,readable,load]);
 const visibleItems=useMemo(()=>itemsScopeKey===key?items:[],[itemsScopeKey,key,items]); const pageCount=Math.max(1,Math.ceil(visibleItems.length/PAGE_SIZE)); useEffect(()=>setPageState(p=>Math.min(Math.max(1,p),pageCount)),[pageCount]); const pageItems=useMemo(()=>visibleItems.slice((page-1)*PAGE_SIZE,page*PAGE_SIZE),[visibleItems,page]);
 // 深链定位：列表按 limit 500 全量载入后走客户端分页，目标 QA 若在其中就翻到它所在页并标记；
 // 不在（被其它筛选排除，或超出单次上限）时给出可见提示，绝不把第一页的别的行冒充成命中。
 // 命中不是终点：定位成功后那条 QA 被改到不满足当前筛选、或被别人过期掉时，要落回未命中
 // 并清掉标记，否则会留下"既不标蓝也不提示"的第三种状态，且再也回不来（stamp 已置）。
 useEffect(()=>{const e=epochRef.current;const id=deepLinkQaId?.trim()??"";if(!id){deepLinkAppliedRef.current="";setFocusedQaId(null);setDeepLinkNotice(null);return;}if(status!=="ready"||itemsScopeKey!==key||!current(key,e))return;const stamp=`${key}\u0000${id}`;const applied=deepLinkAppliedRef.current===stamp;const idx=visibleItems.findIndex((item)=>item.id===id);if(idx<0){deepLinkAppliedRef.current="";setFocusedQaId(null);setDeepLinkNotice(`深链指向的 QA ${id} 不在当前结果中（可能被其它筛选排除，或超出单次 500 条上限）。`);return;}setDeepLinkNotice(null);if(!applied){deepLinkAppliedRef.current=stamp;setPageState(Math.floor(idx/PAGE_SIZE)+1);}setFocusedQaId(id);},[current,deepLinkQaId,itemsScopeKey,key,status,visibleItems]);
 const mutate=useCallback(async(id:string,op:(s:GovernanceScope,signal:AbortSignal)=>Promise<unknown>)=>{const s=scope,k=key,e=epochRef.current;if(!s||!writable||online!==true||busyRef.current)return false;const c=new AbortController();mutationRef.current=c;busyRef.current=true;setMutatingId(id);setError(null);try{await op(s,c.signal);if(c.signal.aborted||!current(k,e))return false;return await load(false);}catch(x){if(c.signal.aborted||!current(k,e)||aborted(x))return false;const view=projectGovernanceError(x);setError(view);if(view.kind==="conflict")await load(true);return false;}finally{if(mutationRef.current===c)mutationRef.current=null;if(current(k,e)){busyRef.current=false;setMutatingId(null);}}},[current,key,load,online,scope,writable]);
 const runBatch=useCallback(async<T,>(id:string,op:(s:GovernanceScope,signal:AbortSignal)=>Promise<T>):Promise<T|null>=>{const s=scope,k=key,e=epochRef.current;if(!s||!writable||online!==true||busyRef.current)return null;const c=new AbortController();mutationRef.current=c;busyRef.current=true;setMutatingId(id);setError(null);try{const result=await op(s,c.signal);if(c.signal.aborted||!current(k,e))return null;await load(false);return result;}catch(x){if(c.signal.aborted||!current(k,e)||aborted(x))return null;const view=projectGovernanceError(x);setError(view);if(view.kind==="conflict")await load(true);return null;}finally{if(mutationRef.current===c)mutationRef.current=null;if(current(k,e)){busyRef.current=false;setMutatingId(null);}}},[current,key,load,online,scope,writable]);
 const setFilters=useCallback((f:QAListFilters)=>{setPageState(1);setFilterState(f);},[]);
 const exportFiltered=useCallback(async(format:QAExportFormat="json"):Promise<string|null>=>{const s=scope,k=key,e=epochRef.current;if(!s||!readable||online!==true)return null;try{const text=await exportQA(s,requestFilters,format);if(!current(k,e))return null;return text;}catch(x){if(aborted(x)||!current(k,e))return null;setError(projectGovernanceError(x));return null;}},[current,key,online,readable,requestFilters,scope]);
 return {status,items:visibleItems,pageItems,page,pageCount,pageSize:PAGE_SIZE,filters,error,mutatingId,truncated,focusedQaId,deepLinkNotice,readOnly:!writable,setPage:(p:number)=>setPageState(Math.max(1,p)),setFilters,refresh:()=>load(false),create:(p:QACreate)=>mutate("create",(s,signal)=>createQA(s,p,{signal})),update:(id:string,p:QAUpdate)=>mutate(id,(s,signal)=>patchQA(s,id,p,{signal})),review:(id:string,p:QAReviewRequest)=>mutate(id,(s,signal)=>reviewQA(s,id,p,{signal})),expire:(id:string,p:DatasetRevisionRequest)=>mutate(id,(s,signal)=>expireQA(s,id,p,{signal})),restore:(id:string,p:DatasetRevisionRequest)=>mutate(id,(s,signal)=>restoreQA(s,id,p,{signal})),addAlternative:(id:string,p:QAAlternativeCreate)=>mutate(id,(s,signal)=>addQAAlternative(s,id,p,{signal})),deleteAlternative:(id:string,a:string,r:number)=>mutate(id,(s,signal)=>deleteQAAlternative(s,id,a,r,{signal})),addNegative:(id:string,p:QANegativeCreate)=>mutate(id,(s,signal)=>addQANegative(s,id,p,{signal})),deleteNegative:(id:string,negId:string,r:number)=>mutate(id,(s,signal)=>deleteQANegative(s,id,negId,r,{signal})),importItems:(payload:QAImportRequest)=>runBatch<QAImportResult>("import",(s,signal)=>importQA(s,payload,{signal})),batchReview:(items:QABatchReviewItem[])=>runBatch<QABatchResult>("batch-review",(s,signal)=>batchReviewQA(s,items,{signal})),batchExpire:(items:QABatchRevisionItem[])=>runBatch<QABatchResult>("batch-expire",(s,signal)=>batchExpireQA(s,items,{signal})),batchRestore:(items:QABatchRevisionItem[])=>runBatch<QABatchResult>("batch-restore",(s,signal)=>batchRestoreQA(s,items,{signal})),exportQA:exportFiltered};
}
