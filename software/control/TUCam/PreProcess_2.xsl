<?xml version="1.0" encoding="UTF-8"?>
<!--.................................................................................................................................
     (c) 2006-2008 Basler Vision Technologies
		 Section: Vision Components
		 Project: GenApi
		 $Header$
		 Author: Hartmut Nebelung, Fritz Dierks
		 
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
	<!--Replace the Group nodes by it's content.-->
	<!-- ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~ -->
	<xsl:template match="Group">
		<xsl:apply-templates select="node()"/>
	</xsl:template>
	
  <!-- ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~ -->
	<!--Strip the Extension node -->
	<!-- ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~ -->
	<xsl:template match="Extension">
  </xsl:template>
	
  <!-- ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~ -->
	<!--Extract embedded EnumEntries -->
	<!-- ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~ -->
	<xsl:template match="Enumeration">
		<!-- Create the EnumEntry elements -->
		<xsl:for-each select="node() | @*">
			<xsl:choose>
				<xsl:when test="name()='EnumEntry'">
					<xsl:apply-templates select="." mode="GenerateElement">
						<xsl:with-param name="EnumerationName" select="../@Name"/>
						<xsl:with-param name="EntryName" select="@Name"/>
						<xsl:with-param name="NameSpace" select="@NameSpace"/>
					</xsl:apply-templates>
				</xsl:when>
			</xsl:choose>
		</xsl:for-each>
		<!-- Create references to the EnumEntry elements -->
		<xsl:copy>
			<xsl:for-each select="node()|@*">
				<xsl:choose>
					<xsl:when test="name()='EnumEntry'">
						<xsl:apply-templates select="." mode="GenerateReference">
							<xsl:with-param name="EnumerationName" select="../@Name"/>
							<xsl:with-param name="EntryName" select="@Name"/>
						</xsl:apply-templates>
					</xsl:when>
					<xsl:otherwise>
						<xsl:apply-templates select="."/>
					</xsl:otherwise>
				</xsl:choose>
			</xsl:for-each>
		</xsl:copy>
	</xsl:template>
	<!--Create an EnumEntry element -->
	<xsl:template match="EnumEntry" mode="GenerateElement">
		<xsl:param name="EntryName"/>
		<xsl:param name="EnumerationName"/>
		<xsl:param name="NameSpace"/>
	    <xsl:copy>
	        <xsl:attribute name="Name">EnumEntry_<xsl:value-of select="$EnumerationName"/>_<xsl:value-of select="$EntryName"/></xsl:attribute>
	        <xsl:attribute name="NameSpace"><xsl:value-of select="$NameSpace"/></xsl:attribute>
	        <xsl:apply-templates select="node()"/>
	        <xsl:if test="0=count(*[name()='Symbolic'])">
	            <xsl:element name="Symbolic" namespace="{$MyNamespace}"><xsl:value-of select="$EntryName"/></xsl:element>
	        </xsl:if>
	    </xsl:copy>
	</xsl:template>
	<!--Create a reference to EnumEntry element -->
	<xsl:template match="EnumEntry" mode="GenerateReference">
		<xsl:param name="EntryName"/>
		<xsl:param name="EnumerationName"/>
		<xsl:element name="pEnumEntry" namespace="{$MyNamespace}">EnumEntry_<xsl:value-of select="$EnumerationName"/>_<xsl:value-of select="$EntryName"/></xsl:element>
	</xsl:template>
	
  <!-- ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~ -->
	<!--Extract the ConfRom Key entries.-->
	<!-- ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~ -->
	<xsl:template match="ConfRom">
		<!-- create int key and text desc elements-->
		<xsl:apply-templates select="IntKey|TextDesc" mode="GenerateElement"/>
		<!-- shallow copy of ConfRom -->
		<xsl:copy>
			<xsl:apply-templates select="@*|Language|Unit|Address|Length|pPort|IntSwissKnife|pAddress"/>
		</xsl:copy>
	</xsl:template>
	<!--Copy an Integer Key or a Textual Descriptor-->
	<xsl:template match="IntKey|TextDesc" mode="GenerateElement">
		<xsl:copy>
			<xsl:attribute name="Name"><xsl:value-of select="@Name"/></xsl:attribute>
			<xsl:attribute name="NameSpace"><xsl:value-of select="@NameSpace"/></xsl:attribute>
			<xsl:element name="p1212Parser" namespace="{$MyNamespace}"><xsl:value-of select="../@Name"/></xsl:element>
			<xsl:element name="Key" namespace="{$MyNamespace}"><xsl:value-of select="."/></xsl:element>
		</xsl:copy>
	</xsl:template>
	
  <!-- ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~ -->
	<!--Extract the StructReg entries.-->
	<!-- ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~ -->
	<!-- Walk the StructEntry elements inside the StructReg elements -->
	<xsl:template match="StructReg">
		<xsl:for-each select="node()">
			<xsl:if test="name()='StructEntry'">
				<xsl:call-template name="StructReg_GenerateMaskedIntElement">
					<xsl:with-param name="NodeName" select="@Name"/>
					<xsl:with-param name="SubNodes" select="node()"/>
				</xsl:call-template>
			</xsl:if>
		</xsl:for-each>
	</xsl:template>
	<!--Create a MaskedInt element -->
	<xsl:template name="StructReg_GenerateMaskedIntElement">
		<xsl:param name="NodeName"/>
		<xsl:param name="SubNodes"/>
		<xsl:element name="MaskedIntReg" namespace="{$MyNamespace}">
      <!--Copy all attributes name and namespace-->
			<xsl:copy-of select="@*"/>
			<!-- Copy all elements which are not StructEntry elements -->
			<xsl:for-each select="../*[name()!='StructEntry'] ">
				<xsl:variable name="CurrentNodeName" select="name()"/>
				<xsl:if test="0=count($SubNodes[name()=$CurrentNodeName])">
					<xsl:copy>
						<xsl:apply-templates select="@*|node()"/>
					</xsl:copy>
				</xsl:if>
			</xsl:for-each>
			<!-- Copy the sub-elements from the StructEntry elements -->
			<xsl:for-each select="$SubNodes">
				<xsl:copy>
					<xsl:apply-templates select="@*|node()"/>
				</xsl:copy>
			</xsl:for-each>
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
