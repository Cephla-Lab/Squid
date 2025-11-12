<?xml version="1.0" encoding="UTF-8"?>
<!--.................................................................................................................................
     (c) 2006-2008 Basler Vision Technologies
		 Section: Vision Components
		 Project: GenApi
		 $Header$
		 Author: Fritz Dierks
		 
     License: This file is published under the license of the EMVA GenICam  Standard Group. 
     A text file describing the legal terms is included in  your installation as 'GenICam_license.pdf'. 
     If for some reason you are missing  this file please contact the EMVA or visit the website
     (http://www.genicam.org) for a full copy.
 
     THIS SOFTWARE IS PROVIDED BY THE EMVA GENICAM STANDARD GROUP "AS IS"
     AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO,  
     THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR  
     PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE EMVA GENICAM STANDARD  GROUP 
     OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,  SPECIAL, 
     EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT  LIMITED TO, 
     PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,  DATA, OR PROFITS; 
     OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY  THEORY OF LIABILITY, 
     WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT  (INCLUDING NEGLIGENCE OR OTHERWISE) 
     ARISING IN ANY WAY OUT OF THE USE  OF THIS SOFTWARE, EVEN IF ADVISED OF THE 
     POSSIBILITY OF SUCH DAMAGE.
     .................................................................................................................................-->
<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">
	<xsl:output method="xml" version="1.0" encoding="utf-8" indent="yes"/>

  <!-- ========================================================== -->
  <!-- Copy everything getting just getting rid of the namespace  -->
  <!-- ========================================================== -->
  <!-- 
      This makes sure the subsequent (more complex) transform can run on any schema version
      as long as the different versions are backward compatible.
  -->
	
	<xsl:template match="/|comment()|processing-instruction()">
		<xsl:copy>
		  <xsl:apply-templates/>
		</xsl:copy>
	</xsl:template>
	
	<xsl:template match="*">
		<xsl:element name="{local-name()}">
		  <xsl:apply-templates select="@*|node()"/>
		</xsl:element>
	</xsl:template>
		
	<xsl:template match="@*">
		<xsl:if test="not(local-name()='schemaLocation')">
			<xsl:attribute name="{local-name()}">
			  <xsl:value-of select="."/>
			</xsl:attribute>
		</xsl:if>
	</xsl:template>

</xsl:stylesheet>
