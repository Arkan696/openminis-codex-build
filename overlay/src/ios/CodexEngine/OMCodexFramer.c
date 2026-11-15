// SPDX-License-Identifier: GPL-3.0-or-later
#include "OMCodexFramer.h"
#include <stdlib.h>
#include <string.h>
bool OMCodexFramerInit(OMCodexFramer *f,size_t limit,size_t totalLimit){
 if(!f || !limit || totalLimit<limit)return false;
 memset(f,0,sizeof(*f));f->bytes=malloc(limit);f->limit=limit;f->totalLimit=totalLimit;
 return f->bytes!=NULL;
}
bool OMCodexFramerFeed(OMCodexFramer *f,const void *input,size_t n,OMCodexLine cb,void *ctx){
 if(!f || f->failed || !f->bytes || (!input && n) || !cb)return false;
 if(n>f->totalLimit-f->total){f->failed=true;return false;}f->total+=n;
 const unsigned char *p=input;
 for(size_t i=0;i<n;i++){
  if(p[i]=='\n'){
   size_t len=f->length;if(len && f->bytes[len-1]=='\r')len--;
   if(len && !cb(f->bytes,len,ctx)){f->failed=true;return false;}f->length=0;
  }else{
   if(f->length==f->limit){f->failed=true;return false;}
   f->bytes[f->length++]=p[i];
  }
 }return true;
}
bool OMCodexFramerFinish(OMCodexFramer *f){return f && !f->failed && f->length==0;}
void OMCodexFramerDestroy(OMCodexFramer *f){if(!f)return;free(f->bytes);memset(f,0,sizeof(*f));}
