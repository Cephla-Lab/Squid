/********************************** ZMC系列控制器  ************************************************
**--------------文件信息--------------------------------------------------------------------------------
**文件名: zmotion.h
**创建人: 郑孝洋
**时间: 20121008
**描述: ZMCDLL 外部接口函数

**------------修订历史记录----------------------------------------------------------------------------
** 修改人:
** 版  本:
** 日　期:
** 描　述:
**
**------------------------------------------------------------------------------------------------------
********************************************************************************************************/


#ifdef  __cplusplus
extern "C" {
#endif


#ifndef _ZMOTION_DLL_H
#define _ZMOTION_DLL_H

#define __stdcall

/*********************************************************
数据类型定义
**********************************************************/

//特殊的定义加在这里
typedef unsigned long long   uint64;

//特殊的定义加在这里
typedef long long   int64;


//#define BYTE           INT8U
//#define WORD           INT16U
//#define DWORD          INT32U
typedef unsigned char  BYTE;
typedef unsigned short  WORD;
//typedef unsigned int  DWORD;
//#define __stdcall
typedef unsigned char  uint8;                   /* defined for unsigned 8-bits integer variable     无符号8位整型变量  */
typedef signed   char  int8;                    /* defined for signed 8-bits integer variable        有符号8位整型变量  */
typedef unsigned short uint16;                  /* defined for unsigned 16-bits integer variable     无符号16位整型变量 */
typedef signed   short int16;                   /* defined for signed 16-bits integer variable         有符号16位整型变量 */
typedef unsigned int   uint32;                  /* defined for unsigned 32-bits integer variable     无符号32位整型变量 */
typedef signed   int   int32;                   /* defined for signed 32-bits integer variable         有符号32位整型变量 */
typedef float          fp32;                    /* single precision floating point variable (32bits) 单精度浮点数（32位长度） */
typedef double         fp64;                    /* double precision floating point variable (64bits) 双精度浮点数（64位长度） */
typedef unsigned int   uint;                  /* defined for unsigned 32-bits integer variable     无符号32位整型变量 */

// 连接类型,
enum ZMC_CONNECTION_TYPE
{
    ZMC_CONNECTION_COM = 1,
    ZMC_CONNECTION_ETH = 2,
    ZMC_CONNECTION_USB = 3,
    ZMC_CONNECTION_PCI = 4,
    ZMC_CONNECTION_LOCAL = 5,
};

//缺省的等待时间
#define ZMC_DEFAULT_TIMEOUT   5000

//串口延时需要更加长一些
#define ZMC_DEFAULT_TIMEOUT_COM   5000


//通道句柄定义
typedef  void* ZMC_HANDLE;



/************************************************/
//错误码 详细的错误码常见说明书或 zerror.h
/************************************************/
#define ERR_OK  0
#define ERROR_OK 0
#define ERR_SUCCESS  0



/*********************************************************
系统状态定义
**********************************************************/
enum ZBASIC_TASKSTATE
{
    TASK_STATE_RUNING = 1,
    TASK_STATE_PAUSE = 3,
    TASK_STATE_STOP = 0,

    /************  下面是trio特殊状态, 暂时不支持  ************/
    //步进
    TASK_STATE_STEP = 2,
    TASK_STATE_PAUSING = 4,
    TASK_STATE_STOPING = 5,

    TASK_STATE_ERROR = 100,//查询的时候如果ID错返回这个值, 脚本初始化发现语法错误也返回这个错误
};

//新增加
#define  SYS_STATE_CANNOT_CONNECT   50 //链接不上



/*********************************************************
函数声明
**********************************************************/

/*************************************************************
Description:    // zvlatin把读取信息.
Input:          //  ilatin        锁存通道编号

Output:         // pWrTimes 判断是否又有锁存
Return:         //错误码
*************************************************************/
int32 __stdcall ZMC_ZVLatinImgGetInfo(ZMC_HANDLE handle, int ilatin, int *pwidth, int *pheight, int *pbites, int* pWrTimes);
/*************************************************************
Description:    // zvlatin把所有数据读取过来, 并填入缓冲 555 格式
Input:          //

Output:         // pBuff 颜色缓冲
Return:         //错误码
*************************************************************/
int32 __stdcall ZMC_ZVLatinImgGet(ZMC_HANDLE handle, int ilatin, int *pwidth, int *pheight, int *pbites, uint8 *pBuff, int ibuffsize);
/*************************************************************
Description:    // 把所有数据读取过来, 并填入缓冲  565 格式
Input:          //

Output:         // pBuff 颜色缓冲
Return:         //错误码
*************************************************************/
int32 __stdcall ZMC_ZVLatinImgGet2(ZMC_HANDLE handle, int ilatin, int *pwidth, int *pheight, int *pbites, uint8 *pBuff, int ibuffsize);

/*************************************************************
Description:    //LINUX LIB库的初始化
Input:          //无
Output:         //
Return:         //BOOL
*************************************************************/
bool __stdcall ZMC_LinuxLibInit();

/*************************************************************
Description:    //与控制器建立链接
Input:          //无
Output:         //卡链接handle
Return:         //错误码
*************************************************************/
int32 __stdcall ZMC_Open(ZMC_CONNECTION_TYPE type, char *pconnectstring ,ZMC_HANDLE * phandle);

/*************************************************************
Description:    //与控制器建立链接, 可以指定连接的等待时间
Input:          //无
Output:         //卡链接handle
Return:         //错误码
*************************************************************/
int32 __stdcall ZMC_FastOpen(ZMC_CONNECTION_TYPE type, char *pconnectstring, uint32 uims ,ZMC_HANDLE * phandle);

/*************************************************************
Description:    //与控制器建立链接， 串口方式.
Input:          //无
Output:         //卡链接handle
Return:         //错误码
*************************************************************/
int32 __stdcall ZMC_OpenCom(uint32 comid, ZMC_HANDLE * phandle);


/*************************************************************
Description:    //可以修改缺省的波特率等设置

uint32 dwByteSize = 8, uint32 dwParity = NOPARITY, uint32 dwStopBits = ONESTOPBIT

Input:          //
Output:         //
Return:         //错误码
*************************************************************/
int32 __stdcall ZMC_SetComDefaultBaud(uint32 dwBaudRate, uint32 dwByteSize, uint32 dwParity, uint32 dwStopBits);
/*************************************************************
Description:    //快速控制器建立链接
Input:          //无
Output:         //卡链接handle
Return:         //错误码
*************************************************************/
int32 __stdcall ZMC_PeakCom(uint32 comid, uint32 uims,ZMC_HANDLE * phandle);

/*************************************************************
Description:    //与控制器建立链接
Input:          //IP地址，字符串的方式输入
Output:         //卡链接handle
Return:         //错误码
*************************************************************/
int32 __stdcall ZMC_OpenEth(char *ipaddr, ZMC_HANDLE * phandle);

/*************************************************************
Description:    //与控制器建立链接
Input:          //IP地址，32位数的IP地址输入, 注意字节顺序
Output:         //卡链接handle
Return:         //错误码
*************************************************************/
int32 __stdcall ZMC_OpenEth2(struct in_addr straddr, ZMC_HANDLE * phandle);

/*************************************************************
Description:    //与控制器建立链接
Input:          //PCI卡编号
Output:         //卡链接handle
Return:         //错误码
*************************************************************/
int32 __stdcall ZMC_OpenPci(uint32 cardnum, ZMC_HANDLE * phandle);

/*************************************************************
Description:    //读取PCI的控制卡个数
Input:          //
Output:         //
Return:         //卡数
*************************************************************/
uint32 __stdcall ZMC_GetMaxPciCards();

/*************************************************************
Description:    //与控制器建立链接, 自动查找COM号
Input:          //COM号范围
Output:         //卡链接handle
Return:         //错误码
*************************************************************/
int32 __stdcall ZMC_SearchAndOpenCom(uint32 uimincomidfind, uint32 uimaxcomidfind,uint* pcomid, uint32 uims, ZMC_HANDLE * phandle);
/*************************************************************
Description:    //与控制器建立链接, 自动搜索网络. 暂时不支持
Input:          //最长等待时间
Output:         //卡链接handle 连接的IP地址
Return:         //错误码
*************************************************************/
int32 __stdcall ZMC_SearchAndOpenEth(char *ipaddr, uint32 uims, ZMC_HANDLE * phandle);

/*************************************************************
Description:    //与控制器建立链接, 自动搜索网络.
Input:          //最长等待时间
Output:         //控制器IP地址, 地址间用空格区分。
Return:         //错误码
*************************************************************/
int32 __stdcall ZMC_SearchEth(char *ipaddrlist,  uint32 addrbufflength, uint32 uims);


/*************************************************************
Description:    //关闭控制器链接
Input:          //卡链接handle
Output:         //
Return:         //错误码
*************************************************************/
int32 __stdcall ZMC_Close(ZMC_HANDLE  handle);

/*************************************************************
Description:    //命令的延时等待时间
Input:          //卡链接handle 毫秒时间
Output:         //
Return:         //错误码
*************************************************************/
int32 __stdcall ZMC_SetTimeOut(ZMC_HANDLE  handle, uint32 timems);

/*************************************************************
Description:    //命令的延时等待时间
Input:          //卡链接handle
Output:         //毫秒时间
Return:         //错误码
*************************************************************/
int32 __stdcall ZMC_GetTimeOut(ZMC_HANDLE  handle, uint32* ptimems);

/*************************************************************
Description:    //读取长时间命令的进度
Input:          //卡链接handle
Output:         //
Return:         //进度， 浮点，
*************************************************************/
float  __stdcall ZMC_GetProgress(ZMC_HANDLE  handle);

/*************************************************************
Description:    //读取连接的类型
Input:          //卡链接handle
Output:         //
Return:         //ZMC_CONNECTION_TYPE
*************************************************************/
uint8  __stdcall ZMC_GetConnectType(ZMC_HANDLE  handle);

/*************************************************************
Description:    //读取连接的名称
Input:          //卡链接handle
Output:         //
Return:         //string
*************************************************************/
const char*  __stdcall ZMC_GetConnectString(ZMC_HANDLE  handle);


//主动上报的回调函数格式
typedef void (*PZMCAutoUpCallBack)(ZMC_HANDLE handle, int32 itypecode, int32 idatalength, uint8 *pdata);

/*************************************************************
Description:    //主动上报回调函数, 需要控制器支持.
Input:          //卡链接handle
Output:         //
Return:         //错误码
*************************************************************/
int32 __stdcall ZMC_SetAutoUpCallBack(ZMC_HANDLE handle, PZMCAutoUpCallBack pcallback);


#if 0
#endif
/***************************************************
    ZBASIC命令，文件相关函数列表
***************************************************/


/*************************************************************
Description:    ////读取系统状态
Input:          //卡链接handle
Output:         //状态 ZBASIC_TASKSTATE
Return:         //错误码
*************************************************************/
int32 __stdcall ZMC_GetState(ZMC_HANDLE handle,uint8 *pstate);

/*************************************************************
Description:    //读取暂停导致的任务号
Input:          //卡链接handle
Output:         //任务号
Return:         //错误码
*************************************************************/
int32 __stdcall ZMC_GetStopPauseTaskid(ZMC_HANDLE handle,uint8 *ptaskid);


/*************************************************************
Description:    ////读取链接控制器的总虚拟轴数
Input:          //卡链接handle
Output:         //
Return:         //轴数，出错0
*************************************************************/
uint8 __stdcall ZMC_GetAxises(ZMC_HANDLE handle);

/*************************************************************
Description:    //通用的命令执行接口,此命令不读取控制器的应答.  当控制器没有缓冲时自动阻赛
Input:          //卡链接handle     pszCommand命令串,  uimswait 最长等待ms时间
Output:         //
Return:         //错误码
*************************************************************/
int32 __stdcall ZMC_ExecuteNoAck(ZMC_HANDLE handle, const char* pszCommand, uint32 uimswait);

/*************************************************************
Description:    //通用的命令执行接口  当控制器没有缓冲时自动阻赛
Input:          //卡链接handle  pszCommand命令串,    uimswait 最长等待ms时间
Output:         //psResponse 接收控制器的执行结果输出
Return:         //错误码
*************************************************************/
int32 __stdcall ZMC_Execute(ZMC_HANDLE handle, const char* pszCommand, uint32 uimswait, char* psResponse, uint32 uiResponseLength);

/*************************************************************
Description:    //等待前面的命令执行结束，控制器执行的应答被丢掉. 当没有缓冲时自动阻赛
Input:          //卡链接handle  uimswait 最长等待ms时间
Output:         //
Return:         //错误码
*************************************************************/
int32 __stdcall ZMC_ExecuteWaitDown(ZMC_HANDLE handle, uint32 uimaxms);

/*************************************************************
Description:    //读取在线命令的应答， 对没有接收应答的命令有用.
                此函数不阻赛
Input:          //卡链接handle  uimax 缓冲长度
Output:         //pbuff 返回读取结果，  puiread 读取的长度，  pbifExcuteDown 是否已经执行结束
Return:         //错误码
*************************************************************/
int32 __stdcall ZMC_ExecuteGetReceive(ZMC_HANDLE handle, char * pbuff, uint32 uimax, uint32 *puiread, uint8 *pbifExcuteDown);


/*************************************************************
Description:    //读取在线命令的当前剩余缓冲
Input:          //卡链接handle
Output:         //
Return:         //剩余空间 出错 - 0
*************************************************************/
uint32 __stdcall ZMC_ExecuteGetRemainBuffSpace(ZMC_HANDLE handle);




/*************************************************************
Description:    //直接命令接口，只支持数组变量, 运动函数,  参数等指令.
Input:          //卡链接handle
Output:         //
Return:         //错误码
*************************************************************/
int32 __stdcall ZMC_DirectCommand(ZMC_HANDLE handle, const char* pszCommand, char* psResponse, uint32 uiResponseLength);



/*************************************************************
Description:    //打下载包ZAR,
Input:          //卡链接handle
                pZpjfilename 项目文件名 带路径
                pZarfilename ZAR文件名
                pPass 软件密码, 绑定APP_PASS  没有密码时pPass = NULL
                uid 绑定控制器唯一ID， 0-不绑定
Output:         //
Return:         //错误码
*************************************************************/
int32 __stdcall ZMC_MakeZar(const char* pZpjfilename, const char* pZarfilename, const char *pPass, uint32 uid);

/*************************************************************
Description:    //打下载包ZAR, 输入二进制的zpj文件
Input:          //卡链接handle
                pzpj 文件缓冲
                pBasDir bas程序文件路径
                pZarfilename ZAR文件名
                pPass 软件密码, 绑定APP_PASS  没有密码时pPass = NULL
                uid 绑定控制器唯一ID， 0-不绑定
Output:         //
Return:         //错误码
*************************************************************/
int32 __stdcall ZMC_MakeZar2(void *pzpj,const char *pBasDir, const char* pZarfilename, const char *pPass, uint32 uid);

/*************************************************************
Description:    //下载包文件
Input:          //卡链接handle
                pfilename  zar文件名
                pfilenameinControl  BASIC系统只有一个包文件，可以不指定.
Output:         //
Return:         //错误码
*************************************************************/
int32 __stdcall ZMC_DownZar(ZMC_HANDLE handle, const char* pfilename, const char* pfilenameinControl);

/*************************************************************
Description:    //下载包文件
Input:          //卡链接handle
                pbuffer     zar文件在内存中的地址
                buffsize    zar文件长度
                pfilenameinControl 控制器上文件的名字 , BASIC系统只有一个包文件，可以不指定.
Output:         //
Return:         //错误码
*************************************************************/
int32 __stdcall ZMC_DownMemZar(ZMC_HANDLE handle, const char* pbuffer, uint32 buffsize, const char* pfilenameinControl);



/*************************************************************
Description:    //运行包
Input:          //卡链接handle
                pfilenameinControl 文件名， 当为NULL的时候运行缺省文件 , BASIC系统只有一个包文件，可以不指定.
Output:         //
Return:         //错误码
*************************************************************/
int32 __stdcall ZMC_RunZarFile(ZMC_HANDLE handle, const char* pfilenameinControl);

/*************************************************************
Description:    //暂停继续运行
Input:          //卡链接handle
Output:         //
Return:         //错误码
*************************************************************/
int32 __stdcall ZMC_Resume(ZMC_HANDLE handle);

/*************************************************************
Description:    //下载到ram中运行
Input:          //卡链接handle
                pfilename zar文件名, 带路径
Output:         //
Return:         //错误码
*************************************************************/
int32 __stdcall ZMC_DownZarToRamAndRun(ZMC_HANDLE handle, const char* pfilename);

/*************************************************************
Description:    //下载包到ram中运行
Input:          //卡链接handle
                pbuffer     zar文件在内存中的地址
                buffsize    zar文件长度
Output:         //
Return:         //错误码
*************************************************************/
int32 __stdcall ZMC_DownMemZarToRamAndRun(ZMC_HANDLE handle, const char* pbuffer, uint32 buffsize);

/*************************************************************
Description:    //打下载包ZAR， 并下载到RAM运行
Input:          //卡链接handle 文件名
                pZpjfilename 项目文件名 带路径
                pPass 软件密码, 绑定APP_PASS  没有密码时pPass = NULL
                uid 绑定控制器唯一ID， 0-不绑定
Output:         //
Return:         //错误码
*************************************************************/
int32 __stdcall ZMC_MakeZarAndRamRun(ZMC_HANDLE handle, const char* pZpjfilename,const  char *pPass, uint32 uid);

/*************************************************************
Description:    //打下载包ZAR， 并下载到RAM运行
Input:          //卡链接handle 文件名
                pzpj 文件缓冲
                pBasDir bas程序文件路径
                pPass 软件密码, 绑定APP_PASS  没有密码时pPass = NULL
                uid 绑定控制器唯一ID， 0-不绑定
Output:         //
Return:         //错误码
*************************************************************/
int32 __stdcall ZMC_MakeZarAndRamRun2(ZMC_HANDLE handle, void *pzpj,const char *pBasDir,const  char *pPass, uint32 uid);

/*************************************************************
Description:    //打下载包ZAR, 并下载到控制器ROM
Input:          //卡链接handle 文件名
                pZpjfilename 项目文件名 带路径
                pPass 软件密码, 绑定APP_PASS  没有密码时pPass = NULL
                uid 绑定控制器唯一ID， 0-不绑定
Output:         //
Return:         //错误码
*************************************************************/
int32 __stdcall ZMC_MakeZarAndDown(ZMC_HANDLE handle, const char* pZpjfilename,const  char *pPass, uint32 uid);

/*************************************************************
Description:    //打下载包ZAR, 并下载到控制器ROM
Input:          //卡链接handle 文件名
                pzpj 文件缓冲
                pBasDir bas程序文件路径
                pPass 软件密码, 绑定APP_PASS  没有密码时pPass = NULL
                uid 绑定控制器唯一ID， 0-不绑定
Output:         //
Return:         //错误码
*************************************************************/
int32 __stdcall ZMC_MakeZarAndDown2(ZMC_HANDLE handle, void *pzpj,const char *pBasDir,const  char *pPass, uint32 uid);

/*************************************************************
Description:    //制作单文件的二进制ZPJ文件
Input:          //pBasfilename basic文件名，带路径
Output:         //pzpj     项目文件缓冲
                  pBasDir  BAS文件路径输出
Return:         //错误码
*************************************************************/
int32 __stdcall ZMC_MakeOneFileZpj(void *pZpjBuff,  char *pBasDir, const char* pBasfilename);

/*************************************************************
Description:    // 根据zdevelop的zpj文本文件来制作二进制zpj文件.
Input:          // ZPJ文本文件，带路径
Output:         // pzpj     项目文件缓冲
                pBasDir  BAS文件路径, 制作ZAR的时候需要.
Return:         //错误码
*************************************************************/
int32 __stdcall ZMC_MakeMultiFileZpj(void *pZpjBuff, char *pBasDir,  const char* pZpjfilename);

/*************************************************************
Description:    //ZPJ文件重新生成, 用于和上传的ZPJ比较是否修改.
Input:          //pZpjfilename 二进制zpj文件名，带路径
Output:         //
                pbuffer 缓冲，缓冲长度不能小于文件长度
Return:         //错误码
*************************************************************/
int32 __stdcall ZMC_MakeRealZpj(const char* pZpjfilename, char* pbuffer, uint32 buffsize);


/*************************************************************
Description:    //ZPJ文件重新生成, 用于和上传的ZPJ比较是否修改.
                  全部在buff里面
Input:          //pBasDir basic文件的路径
                pzpj 缓冲，二进制zpj文件， 同时作为输出
Output:         //
Return:         //错误码
*************************************************************/
int32 __stdcall ZMC_MakeRealZpjMem(const char* pBasDir, void *pzpj);

/*************************************************************
Description:    //制作ZLIB文件
Input:          //pfilenameBas  basic 文件
                pfilenameZlb    zlb 文件
Output:         //
Return:         //错误码
*************************************************************/
int32 __stdcall ZMC_MakeZlib(const char* pfilenameBas, const char* pfilenameZlb);

/*************************************************************
Description:    //获取ZLIB文件的全局描述，包括变量，SUB等  PLC只能获取LBL定义
Input:          //pfilenameZlb  lib文件名，带路径
Output:         //pbuffer 返回全局描述
Return:         //错误码
*************************************************************/
int32 __stdcall ZMC_GetZlibGlobalDefine(const char* pfilenameZlb, char* pbuffer, uint32 buffsize);

/*************************************************************
Description:    //ZLB是BAS文件还是PLC文件
Input:          //
Output:         //ZAR_FILETYPE  2 bas  3- plc
Return:         //
*************************************************************/
int32 __stdcall ZMC_GetZlibFileType(const char* pfilenameZlb, int *pFileType);

/*************************************************************
Description:    //上传zpj，用于PC和控制器文件比较
Input:          //卡链接handle
                pbuffer 接收zpj文件的缓冲 buffsize 缓冲最大长度
Output:         //puifilesize 读取的zpj文件长度
Return:         //错误码
*************************************************************/
int32 __stdcall ZMC_UpCurZpjToMem(ZMC_HANDLE handle, char* pbuffer, uint32 buffsize, uint32* puifilesize);


/*************************************************************
Description:    //暂停
Input:          //卡链接handle
Output:         //
Return:         //错误码
*************************************************************/
int32 __stdcall ZMC_Pause(ZMC_HANDLE handle);

/*************************************************************
Description:    //停止
Input:          //卡链接handle
Output:         //
Return:         //错误码
*************************************************************/
int32 __stdcall ZMC_Stop(ZMC_HANDLE handle);




/*********************************************************
    3次文件功能暂时不支持.
**********************************************************/

/*************************************************************
Description:    //检查文件是否存在
Input:          //卡链接handle 控制器上文件名，不带扩展
Output:         //
Return:         //错误码
*************************************************************/
int32 __stdcall ZMC_Check3File(ZMC_HANDLE handle, const char* pfilenameinControl, uint8 *pbIfExist, uint32 *pFileSize);

/*************************************************************
Description:    //查找控制器上的文件， 文件名为空表示文件不不存在
Input:          //卡链接handle 控制器上文件名，不带扩展
Output:         // 是否存在 文件大小
Return:         //错误码
*************************************************************/
int32 __stdcall ZMC_FindFirst3File(ZMC_HANDLE handle, char* pfilenameinControl, uint32 *pFileSize);

/*************************************************************
Description:    //查找控制器上的文件， 文件名为空表示文件不不存在
Input:          //卡链接handle 控制器上文件名，不带扩展
Output:         // 是否存在 文件大小
Return:         //错误码
*************************************************************/
int32 __stdcall ZMC_FindNext3File(ZMC_HANDLE handle, char* pfilenameinControl, uint32 *pFileSize);

/*************************************************************
Description:    //查找控制器上的当前文件
Input:          //卡链接handle 控制器上文件名，不带扩展
Output:         // 是否存在 文件大小(暂时不支持)
Return:         //错误码
*************************************************************/
int32 __stdcall ZMC_GetCur3File(ZMC_HANDLE handle, char* pfilenameinControl, uint32 *pFileSize);

/*************************************************************
Description:    //删除控制器上的文件
Input:          //卡链接handle 控制器上文件名，不带扩展
Output:         //
Return:         //错误码
*************************************************************/
int32 __stdcall ZMC_Delete3File(ZMC_HANDLE handle, const char* pfilenameinControl);


/*************************************************************
Description:    //删除控制器上的文件
Input:          //卡链接handle
Output:         //
Return:         //错误码
*************************************************************/
int32 __stdcall ZMC_RemoveAll3Files(ZMC_HANDLE handle);

/*************************************************************
Description:    //下载3次文件
Input:          //卡链接handle 文件名, 必须是3次文件
Output:         //
Return:         //错误码
*************************************************************/
int32 __stdcall ZMC_Down3File(ZMC_HANDLE handle, const char* pfilename, const char* pfilenameinControl);

/*************************************************************
Description:    //下载3次文件, 文本文件的长度用strlen 即可
Input:          //卡链接handle buff
Output:         //
Return:         //错误码
*************************************************************/
int32 __stdcall ZMC_DownMem3File(ZMC_HANDLE handle, const char* pbuffer, uint32 buffsize, const char* pfilenameinControl);
/*************************************************************
Description:    //上传3次
Input:          //卡链接handle 内存buff
Output:         //
Return:         //错误码
*************************************************************/
int32 __stdcall ZMC_Up3File(ZMC_HANDLE handle, const char* pfilename, const char* pfilenameinControl);
/*************************************************************
Description:    //上传3次
Input:          //卡链接handle 内存buff 返回实际的文件长度
Output:         //
Return:         //错误码
*************************************************************/
int32 __stdcall ZMC_Up3FileToMem(ZMC_HANDLE handle, char* pbuffer, uint32 buffsize, const char* pfilenameinControl, uint32* puifilesize);

/*************************************************************
Description:    //写用户flash块, float数据
Input:          //卡链接handle
                    uiflashid 	flash块号
                    uinumes		变量个数
Output:         //
Return:         //错误码
*************************************************************/
int32 __stdcall ZMC_FlashWritef(ZMC_HANDLE handle, uint16 uiflashid, uint32 uinumes, float *pfvlue);

/*************************************************************
Description:    //读取用户flash块, float数据
Input:          //卡链接handle
                    uiflashid 	flash块号
                    uibuffnum	缓冲变量个数
Output:         //
                    puinumesread 读取到的变量个数
Return:         //错误码
*************************************************************/
int32 __stdcall ZMC_FlashReadf(ZMC_HANDLE handle, uint16 uiflashid, uint32 uibuffnum, float *pfvlue, uint32* puinumesread);



/*************************************************************
Description:    //读取当前控制器的IP地址,
Input:          //卡链接handle
Output:         //sIpAddr  返回IP地址，  注意:当设置dhcp以后，设置的IP与实际的不一致。
Return:         //错误码
*************************************************************/
int32 __stdcall ZMC_GetCurIpAddr(ZMC_HANDLE handle, char* sIpAddr);


/*************************************************************
Description:    //读取字符串在当前控制器上的类型
Input:          //卡链接handle
Output:         //type  类型，string_types
                  TYPE2 当类型为数组时，代表数组的长度
                  pvalue 变量直接返回值
Return:         //错误码
*************************************************************/
int32 __stdcall ZMC_GetStringType(ZMC_HANDLE handle, const char *sname, uint16 filenum, uint16* type, uint32* type2, double *pvalue);

enum string_types
{
    STRING_USERSUB = 1,
    STRING_VARIABLE = 2,
    STRING_ARRAY = 3,
    STRING_PARA = 4,

    STRING_CMD = 5,
    STRING_KEYWORD = 6, // AS 等关键词

    STRING_LOCAL = 7, //局部定义

    STRING_MODULE = 8, //局部定义

    STRING_UNKOWN = 10,
};


/*************************************************************
Description:    //IO接口 轴使能, 部分控制器不带轴使能的输出
Input:          //卡链接handle
Output:         //状态
Return:         //错误码
*************************************************************/
int32 __stdcall ZMC_SetAxisEnable(ZMC_HANDLE handle, uint8 iaxis, uint8 bifenable);

/*************************************************************
Description:    //IO接口 设置输出
Input:          //卡链接handle 1- IO开
Output:         //状态
Return:         //错误码
*************************************************************/
int32 __stdcall ZMC_SetOutput(ZMC_HANDLE handle, uint16 inum, uint8 iostate);


/*************************************************************
Description:    //IO接口 读取输入
Input:          //卡链接handle
Output:         //状态
Return:         //错误码
*************************************************************/
int32 __stdcall ZMC_GetIn(ZMC_HANDLE handle, uint16 inum, uint8* pistate);



/*************************************************************
Description:    //IO接口 读取输出
Input:          //卡链接handle
Output:         //状态
Return:         //错误码
*************************************************************/
int32 __stdcall ZMC_GetOutput(ZMC_HANDLE handle, uint16 inum, uint8* pistate);


typedef struct
{
    uint8 m_HomeState; //
    uint8 m_AlarmState;
    uint8 m_SDState;
    uint8 m_INPState;
    uint8 m_ElDecState;
    uint8 m_ElPlusState;
    uint8 m_HandWheelAState;
    uint8 m_HandWheelBState;
    uint8 m_EncodeAState; //
    uint8 m_EncodeBState; //
    uint8 m_EMGState; //每个轴都一样
    uint8 m_ClearState; //
    uint8 m_EnableOut;

    //增加软限位信号
    uint8 m_SoftElDecState; //0- 有效
    uint8 m_SoftElPlusState;
    uint8 m_LatchAState; //锁存信号
    uint8 m_LatchBState; //锁存信号
}struct_AxisStates;

/*************************************************************
Description:    //IO接口 读取轴状态
Input:          //卡链接handle
Output:         //状态 struct_AxisStates
Return:         //错误码
*************************************************************/
int32 __stdcall ZMC_GetAxisStates(ZMC_HANDLE handle, uint8 iaxis, struct_AxisStates* pstrstates);


/*************************************************************
Description:    //IO接口 读取AD
Input:          //卡链接handle
Output:         //状态
Return:         //错误码
*************************************************************/
int32 __stdcall ZMC_GetAIn(ZMC_HANDLE handle, uint16 inum, float* pfstate);


/*************************************************************
Description:    //IO接口 读取DA
Input:          //卡链接handle
Output:         //状态
Return:         //错误码
*************************************************************/
int32 __stdcall ZMC_GetDaOut(ZMC_HANDLE handle, uint16 inum, float* pfstate);

/*************************************************************
Description:    //IO接口 设置DA
Input:          //卡链接handle
Output:         //状态
Return:         //错误码
*************************************************************/
int32 __stdcall ZMC_SetDaOut(ZMC_HANDLE handle, uint16 inum, float fstate);

/*************************************************************
Description:    //IO接口 设置输出
Input:          //卡链接handle
Output:         //状态
Return:         //错误码
*************************************************************/
int32 __stdcall ZMC_SetOutAll(ZMC_HANDLE handle, uint16 inumfirst,uint16 inumend, uint32 istate);

/*************************************************************
Description:    //IO接口 读取输入口
Input:          //卡链接handle
Output:         //状态
Return:         //错误码
*************************************************************/
int32 __stdcall ZMC_GetInAll(ZMC_HANDLE handle, uint16 inumfirst,uint16 inumend, uint32* pistate);


/*************************************************************
Description:    //IO接口 读取输出口
Input:          //卡链接handle
Output:         //状态
Return:         //错误码
*************************************************************/
int32 __stdcall ZMC_GetOutAll(ZMC_HANDLE handle, uint16 inumfirst,uint16 inumend, uint32* pistate);






/*************************************************************
Description:    //modbus寄存器操作 对应 modbus_bit
Input:          //卡链接handle 寄存器地址  inum bit个数
Output:         //
Return:         //错误码
*************************************************************/
uint32 __stdcall ZMC_Modbus_Set0x(ZMC_HANDLE handle, uint16 start, uint16 inum, uint8* pdata);
/*************************************************************
Description:    //modbus寄存器操作 对应 modbus_bit
Input:          //卡链接handle 寄存器地址  inum bit个数
Output:         //
Return:         //错误码
*************************************************************/
uint32 __stdcall ZMC_Modbus_Get0x(ZMC_HANDLE handle, uint16 start, uint16 inum, uint8* pdata);

/*************************************************************
Description:    //4x类型读写，对应控制器modbus_reg读写
Input:          //卡链接handle  inum reg个数
Output:         //
Return:         //错误码
*************************************************************/
uint32 __stdcall ZMC_Modbus_Get4x(ZMC_HANDLE handle, uint16 start, uint16 inum, uint16* pdata);

/*************************************************************
Description:    //4x类型读写，对应控制器modbus_reg读写
Input:          //卡链接handle  inum reg个数
Output:         //
Return:         //错误码
*************************************************************/
uint32 __stdcall ZMC_Modbus_Set4x(ZMC_HANDLE handle, uint16 start, uint16 inum, uint16* pdata);

/*************************************************************
Description:    //浮点数类型读写，对应控制器modbus_ieee读写
Input:          //卡链接handle  inum 浮点数个数
Output:         //
Return:         //错误码
*************************************************************/
uint32 __stdcall ZMC_Modbus_SetIEEE(ZMC_HANDLE handle, uint16 start, uint16 inum, float* pdata);

/*************************************************************
Description:    //浮点数类型读写，对应控制器modbus_ieee读写
Input:          //卡链接handle  inum 浮点数个数
Output:         //
Return:         //错误码
*************************************************************/
uint32 __stdcall ZMC_Modbus_GetIEEE(ZMC_HANDLE handle, uint16 start, uint16 inum, float* pdata);

/*************************************************************
Description:    //32位整数类型读写，对应控制器modbus_long读写
Input:          //卡链接handle  inum long个数
Output:         //
Return:         //错误码
*************************************************************/
uint32 __stdcall ZMC_Modbus_SetLong(ZMC_HANDLE handle, uint16 start, uint16 inum, float* pdata);
/*************************************************************
Description:    //32位整数类型读写，对应控制器modbus_long读写
Input:          //卡链接handle  inum long个数
Output:         //
Return:         //错误码
*************************************************************/
uint32 __stdcall ZMC_Modbus_GetLong(ZMC_HANDLE handle, uint16 start, uint16 inum, float* pdata);



/*************************************************************
Description:    // 寄存器操作  一次长度不要超过200字节
Input:          //卡链接handle 寄存器地址
                pRegName "D" "M" "VR" "VR_INT" "V" "Z" "X" "Y" "T" "C" "DT"
                iRegbites  0-缺省， "T" "C" 有用

Output:         //
Return:         //错误码
*************************************************************/
uint32 __stdcall ZMC_RegisterRead(ZMC_HANDLE handle, const char *pRegName, uint8 iRegbites, uint32 start, uint16 inum, uint8* pdata);
/*************************************************************
Description:    // 寄存器操作  一次长度不要超过200字节
Input:          //卡链接handle 寄存器地址
                pRegName "D" "M" "VR" "VR_INT" "V" "Z"   "Y" "T" "C" "DT"
                iRegbites  0-缺省， "T" "C" 有用

Output:         //
Return:         //错误码
*************************************************************/
uint32 __stdcall ZMC_RegisterWrite(ZMC_HANDLE handle, const char *pRegName, uint8 iRegbites, uint32 start, uint16 inum, uint8* pdata);

/*************************************************************
Description:    //把错误码转成描述字符串
Input:          //应答的消息
Output:         //
Return:         //错误码
*************************************************************/
const char* ZMC_GetErrcodeDescription(int32 ierrcode);

/*************************************************************
Description:    //检查程序语法

暂时不提供

Input:          //错误字符串缓存，至少1024字节
Output:         //
Return:         //错误码
*************************************************************/
int32 __stdcall ZMC_CheckProgramSyntax(const char *pzpjfilename, char *sError);


//轴特征位
enum AXIS_FEATURE_BIT
{
    AXIS_FEATURE_STEPPER = 0, //  步进  脉冲方向
    AXIS_FEATURE_STEPPER_DOUBLE = 1, //  步进 双脉冲
    AXIS_FEATURE_STEPPER_AB = 2, //  步进 A, B相

    AXIS_FEATURE_ENCODER = 4, // 支持配置为编码器, 正交,
    AXIS_FEATURE_ENCODER_PULDIR = 5, // 脉冲方向方式的编码器
    AXIS_FEATURE_ENCODER_DOUBLE = 6, // 双脉冲编码器

    AXIS_FEATURE_ENCODER_SSI = 7, //
    AXIS_FEATURE_ENCODER_TAMA = 8, //
    AXIS_FEATURE_ENCODER_ENDAT = 9, //


    AXIS_FEATURE_WITH_EZ = 10, // 有EZ输入，可以和步进一起使用
    AXIS_FEATURE_SERVODAC = 11, // dac 伺服
    AXIS_FEATURE_STEPCODER = 12, // 特殊的类型 步进和编码器一起使用

    AXIS_FEATURE_CANOPEN = 13, //  后面的暂时不支持
    //AXIS_FEATURE_ZCAN = 14, // 支持配置为ZCAN, 这个可以和其他的类型一起合并


};

/*************************************************************
Description:    //读取控制器轴规格
Input:          //卡链接handle
                  iaxis     轴号
Output:         //pfeatures 规格
Return:         //错误码
*************************************************************/
int32 __stdcall ZMC_GetAxisFeatures(ZMC_HANDLE handle, uint8 iaxis, uint32 *pfeatures);


#define SUPPORTBIT_PLC 0
#define SUPPORTBIT_ROM 1
#define SUPPORTBIT_UDP 2 //udp与tcp并行的功能
#define SUPPORTBIT_GCODE 3
#define SUPPORTBIT_HMI 4  //超级PLC功能
#define SUPPORTBIT_MODBUSUDP 5  // modbus udp 的功能

typedef  struct
{
    //主要规格
    uint8   m_bIfSupportBasic; //
    uint8   m_bIfSupportPLC; // 0 是否支持PLC程序,  1, 是否支持ROM,  2, 是否支持UDP， 3，是否支持G代码
    uint8   m_bIfSupportRamRun; // 下载到RAM运行
    uint8   m_bIfLocked; // 是否LOCK

    //轴数规格
    uint16  m_MaxVirtuAxises;
    uint8   m_MaxAxises;
    uint8   m_MaxBreakes;  // 最多断点数
    //IO规格
    uint8   m_MaxInController;
    uint8   m_MaxOutController;
    uint8   m_MaxAdController;
    uint8   m_MaxDaController;

    //存储规格
    uint32  m_MaxProgramSpaceinKB;  // 总程序空间
    uint32  m_MaxNandSpaceinKB;
    uint32  m_MaxNandSpaceRemaininKB; //剩余的空间

    //modbus寄存器规格
    uint16  m_MaxModbusBits;
    uint16  m_MaxModbusRegs;

    //下面为BASIC规格
    uint16  m_MaxFiles;     //系统支持程序文件数
    uint8   m_Max3Files;
    //uint8   m_bIfSupport3File; // 3次文件.
    uint8   m_bReserve;

    //trio兼容
    uint32  m_MaxTable;
    uint32  m_MaxVr;

    uint16  m_MaxTaskes;    //系统任务数
    uint16  m_MaxTimeres;   //最大TIMES数

    uint16  m_MaxVarNum; //    变量数
    uint16  m_MaxArrayNum; //

    uint32  m_MaxArraySpace; //

    uint16  m_MaxSubes; //
    //uint16  m_MaxStackes; //      堆栈层数
    uint8  m_MaxStackes; //   改成8位
    uint8  m_MaxScopes; //示波器通道数

    //uint16  m_MaxExpressionLayeres; //  表达式复杂程度
    uint8  m_MaxExpressionLayeres;
    uint8  m_Reserve81;

    //uint16  m_MaxLabelChares;  //名称字符数
    uint8  m_MaxLabelChares;
    uint8  m_Reserve82; //预留

}struct_SysMaxSpecification;

/*************************************************************
Description:    //读取控制器规格
Input:          //卡链接handle
Output:         //
Return:         //错误码
*************************************************************/
int32 __stdcall ZMC_GetSysSpecification(ZMC_HANDLE handle, struct_SysMaxSpecification *pspeci);

/*************************************************************
Description:    //读取脚本输出的信息
Input:          //卡链接handle
Output:         //
Return:         //错误码
*************************************************************/
int32 __stdcall ZMC_ReadMessage(ZMC_HANDLE handle, char * pbuff, uint32 uimax, uint32 *puiread);


/*************************************************************
Description:    //单步执行
Input:          //
// 单步定义
enum ZBASIC_STEPMODE
{
    STEP_MODE_NONE = 0,
    STEP_MODE_IN = 1,  // 跳到里面
    STEP_MODE_NEXT = 2,  // 跳到 下一个
    STEP_MODE_OUT = 3,  // 跳到 上层
    STEP_MODE_SPECIALLINE = 4,  // 跳到 指定行
};
                ifilenum:指定行的时候使用
Output:         //
Return:         //错误码
*************************************************************/
int32 __stdcall ZMC_StepRun(ZMC_HANDLE handle, uint16 taskid, uint8 Stepmode, uint8 bifBreakAll, uint8 ifilenum, uint32 iLineNum);


enum ZBASIC_STEPMODE
{
    STEP_MODE_NONE = 0,
    STEP_MODE_IN = 1,  // 跳到里面
    STEP_MODE_NEXT = 2,  // 跳到 下一个
    STEP_MODE_OUT = 3,  // 跳到 上层
    STEP_MODE_SPECIALLINE = 4,  // 跳到 指定行
};


typedef struct
{

    //硬件ID
    uint32 m_hardid;

    uint16 m_cardid; // 对编号一致

    //
    uint8 m_imaxin;
    uint8 m_imaxout;
    uint8 m_imaxad;
    uint8 m_imaxda;

    //轴数，非0表示带轴
    uint8 m_iAxises;

}struct_ChildCardInfo;


/*************************************************************
Description:    //取消当前正在执行的命令
Input:          //卡链接handle
Output:         //
Return:         //错误码
*************************************************************/
int32 __stdcall ZMC_CancelOnline(ZMC_HANDLE handle);


/*************************************************************
Description:    //断点, LIB文件里面不能增加断点.
Input:          //卡链接handle 行号 从0 开始编号
Output:         //
Return:         //错误码
*************************************************************/
int32 __stdcall ZMC_BreakAdd(ZMC_HANDLE handle, const char* filenamenoext, uint32 ilinenum, uint8 bifPauseAll);

/*************************************************************
Description:    //断点, LIB文件里面不能增加断点.
Input:          //卡链接handle 行号 从0 开始编号
Output:         //
Return:         //错误码
*************************************************************/
int32 __stdcall ZMC_BreakDel(ZMC_HANDLE handle, const char* filenamenoext, uint32 ilinenum);

/*************************************************************
Description:    //断点清除
Input:          //卡链接handle
Output:         //
Return:         //错误码
*************************************************************/
int32 __stdcall ZMC_BreakClear(ZMC_HANDLE handle);


/*************************************************************
Description:    //全局数组修改
Input:          //卡链接handle
Output:         // 全局数组名 起始索引
Return:         //错误码
*************************************************************/
int32 __stdcall ZMC_ArrayWritef(ZMC_HANDLE handle, char *sname, uint32 uistartIndex, uint32 uinumes, float *pfValue);
/*************************************************************
Description:    //全局数组读取
Input:          //卡链接handle
Output:         // 全局数组名 起始索引
Return:         //错误码
*************************************************************/
int32 __stdcall ZMC_ArrayReadf(ZMC_HANDLE handle, char *sname, uint32 uistartIndex, uint32 uinumes, float *pfValue);

/*************************************************************
Description:    //�ӿ���Ϣ��ȡ
Input:          //������handle
Output:         //
Return:         //������
*************************************************************/
int32 __stdcall ZMC_EnumChildCard(ZMC_HANDLE handle, uint16 lastid, struct_ChildCardInfo *pCard);


#endif

#ifdef  __cplusplus
    }
#endif


