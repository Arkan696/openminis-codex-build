#include "../src/ios/CodexEngine/OMCodexFramer.h"
#include <assert.h>
#include <string.h>
static int count;static char last[128];
static bool got(const unsigned char *p,size_t n,void *ctx){(void)ctx;if(n>=sizeof(last))return false;memcpy(last,p,n);last[n]=0;count++;return true;}
int main(void){
 OMCodexFramer f;assert(OMCodexFramerInit(&f,16,128));
 const char *tail="1}\r\n{\"b\":2}\n";
 assert(OMCodexFramerFeed(&f,"{\"a\":",5,got,0));assert(OMCodexFramerFeed(&f,tail,strlen(tail),got,0));assert(count==2);assert(strcmp(last,"{\"b\":2}")==0);assert(OMCodexFramerFinish(&f));OMCodexFramerDestroy(&f);
 assert(OMCodexFramerInit(&f,4,16));assert(!OMCodexFramerFeed(&f,"12345\n",6,got,0));OMCodexFramerDestroy(&f);
 assert(OMCodexFramerInit(&f,4,5));assert(!OMCodexFramerFeed(&f,"1\n2\n3\n",6,got,0));OMCodexFramerDestroy(&f);
 assert(OMCodexFramerInit(&f,16,64));assert(OMCodexFramerFeed(&f,"{\"x\":1}",7,got,0));assert(!OMCodexFramerFinish(&f));OMCodexFramerDestroy(&f);
 const char utf8[]="{\"t\":\"\xe9\x9b\xaa\"}\n";
 assert(OMCodexFramerInit(&f,32,128));for(size_t i=0;i<strlen(utf8);i++)assert(OMCodexFramerFeed(&f,utf8+i,1,got,0));assert(OMCodexFramerFinish(&f));assert(strcmp(last,"{\"t\":\"\xe9\x9b\xaa\"}")==0);OMCodexFramerDestroy(&f);
 return 0;
}
