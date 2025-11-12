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
<xsl:stylesheet version="1.0" xmlns:gen_1_0="http://www.genicam.org/GenApi/Version_1_0" xmlns:gen_1_1="http://www.genicam.org/GenApi/Version_1_1" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">
	<xsl:output method="xml" version="1.0" encoding="utf-8" indent="yes"/>

  <!-- ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~ -->
  <!-- Some preparateions and the main template -->
  <!-- ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~ -->

  <!-- Define a variable containing the default name space of the processed XML file -->
  <xsl:variable name="MyNamespace" select="RegisterDescription/namespace::*[local-name()='']" />

  <!--Main template: looks for the RegisterDescription-->
	<xsl:template match="/">
		<xsl:apply-templates select="RegisterDescription"/>
  </xsl:template>

  <!-- ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~ -->
  <!--Replace Bit entry by MSB and LSB -->
  <!-- ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~ -->
  <!-- Example: <Bit>12</Bit>   is converted to <MSB>12</MSB><LSB>12</LSB> -->
  <xsl:template match="Bit">
    <xsl:element name="LSB" namespace="{$MyNamespace}">
      <xsl:value-of select="text()"/>
    </xsl:element>
    <xsl:element name="MSB" namespace="{$MyNamespace}">
      <xsl:value-of select="text()"/>
    </xsl:element>
  </xsl:template>

  <!-- ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~ -->
  <!--Extract embedded SwissKnifes -->
  <!-- ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~ -->
  <xsl:template match="Register|IntReg|MaskedIntReg|FloatReg|StringReg">
    <!-- Create SwissKnife elements -->
    <xsl:for-each select="IntSwissKnife">
      <xsl:apply-templates select="." mode="GenerateElement">
        <xsl:with-param name="Name" select="concat('_',generate-id())"/>
      </xsl:apply-templates>
    </xsl:for-each>
    <!-- Create references to the SwissKnife elements -->
    <xsl:copy>
      <xsl:for-each select="node() | @*">
        <xsl:choose>
          <xsl:when test="name()='IntSwissKnife'">
            <xsl:apply-templates select="." mode="GenerateReference">
              <xsl:with-param name="Name" select="concat('_',generate-id())"/>
            </xsl:apply-templates>
          </xsl:when>
          <xsl:otherwise>
            <xsl:apply-templates select="."/>
          </xsl:otherwise>
        </xsl:choose>
      </xsl:for-each>
    </xsl:copy>
  </xsl:template>
  <!--Create a SwissKnife element -->
  <xsl:template match="SwissKnife|IntSwissKnife" mode="GenerateElement">
    <xsl:param name="Name"/>
    <xsl:copy>
      <xsl:attribute name="Name">
        <xsl:value-of select="$Name"/>
      </xsl:attribute>
      <xsl:attribute name="NameSpace">Custom</xsl:attribute>
      <xsl:apply-templates select="node()"/>
    </xsl:copy>
  </xsl:template>
  <!--Create a reference to SwissKnife element uns the Name parameter-->
  <xsl:template match="SwissKnife|IntSwissKnife" mode="GenerateReference">
    <xsl:param name="Name"/>
    <xsl:element name="pAddress" namespace="{$MyNamespace}">
      <xsl:value-of select="$Name"/>
    </xsl:element>
  </xsl:template>

  <!-- ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~ -->
  <!--Copy any other attribute or element which are not matched by the above rules.-->
  <!-- ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~ -->
  <xsl:template match="@*|node()">
    <xsl:copy>
      <xsl:apply-templates select="@*|node()"/>
    </xsl:copy>
  </xsl:template>
  
</xsl:stylesheet>
