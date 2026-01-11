<?xml version="1.0" encoding="UTF-8"?>
<!--.................................................................................................................................
     (c) 2006-2008 Basler Vision Technologies
		 Section: Vision Components
		 Project: GenApi
		 $Header$
		 Author: Jan Becvar
		 
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
  <!--Extract Constants/Expressions -->
  <!-- ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~ -->
  <xsl:template match="IntSwissKnife|IntConverter|SwissKnife|Converter">
    <!-- Create pVariable elements -->
    <xsl:for-each select="Constant">
      <xsl:apply-templates select="." mode="GenerateElement">
        <xsl:with-param name="Name">_<xsl:value-of select="../@Name"/>_<xsl:value-of select="@Name"/></xsl:with-param>
      </xsl:apply-templates>
    </xsl:for-each>
    <xsl:for-each select="Expression">
      <xsl:apply-templates select="." mode="GenerateElement">
        <xsl:with-param name="Name">_<xsl:value-of select="../@Name"/>_<xsl:value-of select="@Name"/></xsl:with-param>
      </xsl:apply-templates>
    </xsl:for-each>
    <!-- Create references to the (Int)SwissKnife elements -->
    <xsl:copy>
      <xsl:for-each select="node() | @*">
        <xsl:choose>
          <xsl:when test="name()='Constant'">
            <xsl:apply-templates select="." mode="GenerateReference">
              <xsl:with-param name="Name">_<xsl:value-of select="../@Name"/>_<xsl:value-of select="@Name"/></xsl:with-param>
            </xsl:apply-templates>
          </xsl:when>
          <xsl:when test="name()='Expression'">
            <xsl:apply-templates select="." mode="GenerateReference">
              <xsl:with-param name="Name">_<xsl:value-of select="../@Name"/>_<xsl:value-of select="@Name"/></xsl:with-param>
            </xsl:apply-templates>
          </xsl:when>
          <xsl:otherwise>
            <xsl:apply-templates select="."/>
          </xsl:otherwise>
        </xsl:choose>
      </xsl:for-each>
    </xsl:copy>
  </xsl:template>
  <!--Create an Integer element from Constant -->
  <xsl:template match="Constant" mode="GenerateElement">
    <xsl:param name="Name"/>
      <xsl:variable name="TargetNode">
                  <xsl:choose>
          <xsl:when test="name(..)='IntSwissKnife' or name(..)='IntConverter'">Integer</xsl:when>
          <xsl:otherwise>Float</xsl:otherwise>
        </xsl:choose>
      </xsl:variable>
   <xsl:element name="{$TargetNode}" namespace="{$MyNamespace}">
      <xsl:attribute name="Name">
        <xsl:value-of select="$Name"/>
      </xsl:attribute>
      <xsl:attribute name="NameSpace">Custom</xsl:attribute>
       <xsl:element name="Value">
      <xsl:value-of select="text()"/>
       </xsl:element>
    </xsl:element>
  </xsl:template>
  <!--Create an (Int)SwissKnife element from Expression -->
  <xsl:template match="Expression" mode="GenerateElement">
    <xsl:param name="Name"/>
      <xsl:variable name="TargetNode">
                  <xsl:choose>
          <xsl:when test="name(..)='IntSwissKnife' or name(..)='IntConverter'">IntSwissKnife</xsl:when>
          <xsl:otherwise>SwissKnife</xsl:otherwise>
        </xsl:choose>
      </xsl:variable>
   <xsl:element name="{$TargetNode}" namespace="{$MyNamespace}">
      <xsl:attribute name="Name">
        <xsl:value-of select="$Name"/>
      </xsl:attribute>
      <xsl:attribute name="NameSpace">Custom</xsl:attribute>
       <xsl:copy-of select="../pVariable"/>
    <xsl:for-each select="../Constant">
        <xsl:element name="pVariable">
            <xsl:attribute name="Name"><xsl:value-of select="@Name"/></xsl:attribute>
            <xsl:text>_</xsl:text>
            <xsl:value-of select="../@Name"/>
            <xsl:text>_</xsl:text>
            <xsl:value-of select="@Name"/>
        </xsl:element>
    </xsl:for-each>
    <xsl:for-each select="preceding-sibling::Expression">
        <xsl:element name="pVariable">
            <xsl:attribute name="Name"><xsl:value-of select="@Name"/></xsl:attribute>
            <xsl:text>_</xsl:text>
            <xsl:value-of select="../@Name"/>
            <xsl:text>_</xsl:text>
            <xsl:value-of select="@Name"/>
        </xsl:element>
    </xsl:for-each>
        <xsl:element name="Formula">
      <xsl:value-of select="text()"/>
       </xsl:element>
   </xsl:element>
  </xsl:template>
 <!--Create a reference to the node generated for the Constant/Expression-->
  <xsl:template match="Constant|Expression" mode="GenerateReference">
    <xsl:param name="Name"/>
    <xsl:element name="pVariable" namespace="{$MyNamespace}">
        <xsl:attribute name="Name">
            <xsl:value-of select="@Name"/>
        </xsl:attribute>
      <xsl:value-of select="$Name"/>
    </xsl:element>
  </xsl:template>
 
  <!-- ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~ -->
  <!--Extract pIndex/@Offset -->
  <!-- ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~ -->
  <xsl:template match="Register[pIndex[not(@pOffset)]]|IntReg[pIndex[not(@pOffset)]]|MaskedIntReg[pIndex[not(@pOffset)]]|FloatReg[pIndex[not(@pOffset)]]|StringReg[pIndex[not(@pOffset)]]">
    <xsl:for-each select="pIndex[@Offset]">
      <xsl:apply-templates select="." mode="GenerateElement">
        <xsl:with-param name="Name">_<xsl:value-of select="../@Name"/>_IndexOffset_<xsl:value-of select="generate-id()"/></xsl:with-param>
        <xsl:with-param name="Value"><xsl:value-of select="@Offset"/></xsl:with-param>
      </xsl:apply-templates>
    </xsl:for-each>
    <xsl:for-each select="pIndex[not(@Offset) and not (@pOffset)]">
      <xsl:apply-templates select="." mode="GenerateElement">
        <xsl:with-param name="Name">_<xsl:value-of select="../@Name"/>_IndexOffset_<xsl:value-of select="generate-id()"/></xsl:with-param>
        <xsl:with-param name="Value"><xsl:value-of select="../Length"/></xsl:with-param>
      </xsl:apply-templates>
    </xsl:for-each>
    <!-- Create references to the pOffset elements -->
    <xsl:copy>
      <xsl:for-each select="node() | @*">
        <xsl:choose>
          <xsl:when test="name()='pIndex' and not(@pOffset)">
            <xsl:apply-templates select="." mode="GenerateReference">
              <xsl:with-param name="Name">_<xsl:value-of select="../@Name"/>_IndexOffset_<xsl:value-of select="generate-id()"/></xsl:with-param>
            </xsl:apply-templates>
          </xsl:when>
          <xsl:otherwise>
            <xsl:apply-templates select="."/>
          </xsl:otherwise>
        </xsl:choose>
      </xsl:for-each>
    </xsl:copy>
  </xsl:template>
   <!--Create an Integer from @Offset -->
  <xsl:template match="pIndex" mode="GenerateElement">
    <xsl:param name="Name"/>
    <xsl:param name="Value"/>
   <xsl:element name="Integer" namespace="{$MyNamespace}">
      <xsl:attribute name="Name">
        <xsl:value-of select="$Name"/>
      </xsl:attribute>
      <xsl:attribute name="NameSpace">Custom</xsl:attribute>
       <xsl:element name="Value"><xsl:value-of select="$Value"/></xsl:element>
   </xsl:element>
  </xsl:template>
 <!--Create a reference to the node generated for the @Offset-->
  <xsl:template match="pIndex" mode="GenerateReference">
    <xsl:param name="Name"/>
    <xsl:element name="pIndex" namespace="{$MyNamespace}">
        <xsl:attribute name="pOffset">
            <xsl:value-of select="$Name"/>
        </xsl:attribute>
      <xsl:value-of select="."/>
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
